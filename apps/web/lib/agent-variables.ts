/**
 * The variables a prompt may cite, and where each one sits in the text.
 *
 * The list comes from the API (`/catalog/prompt-variables`), the same table the
 * engine honours, so a variable added on the server is recognised here without
 * this file changing. What lives here is the scanner: which bracketed spans
 * count as a citation, which of them the release has no variable for, and where
 * a caret may come to rest.
 *
 * Nothing here writes to the prompt. A pasted prompt is read, never rewritten:
 * a citation is recognised, an unknown one is flagged, and the text is the
 * writer's.
 */
import { api } from "@/lib/api";
import type { ClientResource, PipelineStage } from "@/types";

export type VariableKind = "tool" | "block" | "stage" | "resource" | "control";

export type PromptVariable = {
  kind: VariableKind;
  value: string;
  token?: string | null;
  template?: string | null;
  aliases: string[];
  picker?: string | null;
};

export type VariableSpan = {
  /** Where the citation sits in the text. `end` is exclusive. */
  start: number;
  end: number;
  raw: string;
  kind: VariableKind | "unknown";
  /** The machine name behind it: a tool name, a block key, a stage or resource name. */
  value: string;
  /** False when the release has no variable for this: painted red, left editable. */
  known: boolean;
  /** A citation the writer still has to finish, such as a stage left as "...". */
  draft: boolean;
};

let catalogRequest: Promise<PromptVariable[]> | null = null;

/** The server's table of variables, fetched once per session. */
export function promptVariables(): Promise<PromptVariable[]> {
  if (!catalogRequest) {
    catalogRequest = api<PromptVariable[]>("/catalog/prompt-variables").catch((error) => {
      // Never cache a failure: a later mount may well succeed.
      catalogRequest = null;
      throw error;
    });
  }
  return catalogRequest;
}

/** Case- and accent-insensitive, the way the engine matches a marker to its table. */
export function fold(value: string): string {
  return value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
}

const SPAN_RE = /\[([^[\]]*)\]/g;
/** Longest span taken for a citation. Past that it is prose or an accident. */
const MAX_SPAN = 122;
/** A bracket holding any of these is not a citation: code, quotes, a link target. */
const NOISE_RE = /["'`=<>{}()|\\]/;
/** A name the writer left as an ellipsis, which is a draft, not a citation. */
const DRAFT_NAMES = new Set(["", "...", "…", "-", "—"]);

/**
 * Whether a bracketed span nobody claims is trying to be a variable.
 *
 * A prompt illustrates itself with brackets inside the words of a sample reply
 * ("tengo disponibilidad este [Día 1]"), so an unclaimed span is only reported
 * when it is named like a variable: a value after a colon, or a phrase in
 * capitals. Everything else is the writer's own text and stays that way.
 */
export function readsAsVariable(inner: string, following = ""): boolean {
  const text = inner.trim();
  if (!text || text.length + 2 > MAX_SPAN) return false;
  if (NOISE_RE.test(text)) return false;
  if (following === "(") return false; // a markdown link, not a citation
  if (text.includes(":")) return true;
  return text === text.toUpperCase() && text.split(/\s+/).length >= 2;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

type Match = { kind: VariableKind; value: string };

/**
 * The catalog compiled for matching: every marker and alias by its folded text,
 * and every variable that takes a name as a pattern that accepts any.
 */
function compile(catalog: PromptVariable[] | null | undefined) {
  const exact = new Map<string, Match>();
  const templates: { pattern: RegExp; match: Match }[] = [];
  for (const row of catalog ?? []) {
    const match: Match = { kind: row.kind, value: row.value };
    if (row.token) {
      exact.set(fold(row.token), match);
      for (const alias of row.aliases) exact.set(fold(alias), match);
    }
    if (row.template) {
      const [head = "", tail = ""] = row.template.split("{name}");
      templates.push({
        pattern: new RegExp(`^${escapeRegExp(fold(head))}[^\\]]+${escapeRegExp(fold(tail))}$`),
        match,
      });
    }
  }
  return { exact, templates };
}

/** The name a `[Etapa: name]` or `[Recurso: name]` carries. */
function namedValue(raw: string): string {
  const colon = raw.indexOf(":");
  return colon === -1 ? "" : raw.slice(colon + 1, raw.lastIndexOf("]")).trim();
}

/**
 * A stage or a resource is only unknown once its list has arrived: while it is
 * still loading, an unrecognised name is not evidence of a mistake.
 */
function existsIn(
  name: string,
  list: { name: string; is_active?: boolean }[] | undefined,
): boolean {
  if (list === undefined) return true;
  const folded = fold(name);
  return list.some((row) => fold(row.name) === folded && row.is_active !== false);
}

export function parseVariables(
  text: string,
  options: { catalog?: PromptVariable[] | null; stages?: PipelineStage[]; resources?: ClientResource[] } = {},
): VariableSpan[] {
  const { catalog = null, stages, resources } = options;
  const { exact, templates } = compile(catalog);
  const spans: VariableSpan[] = [];

  for (const match of text.matchAll(SPAN_RE)) {
    const raw = match[0];
    const start = match.index ?? 0;
    const end = start + raw.length;
    const base = { start, end, raw };

    // A variable the table knows is recognised whatever it looks like: the shape
    // rules below exist to decide what to do with a bracket nobody claims, and
    // must not be able to hide a real citation.
    const known = exact.get(fold(raw));
    if (known) {
      spans.push({ ...base, kind: known.kind, value: known.value, known: true, draft: false });
      continue;
    }

    const template = templates.find((row) => row.pattern.test(fold(raw)));
    if (template) {
      const name = namedValue(raw);
      const draft = DRAFT_NAMES.has(name);
      const exists = draft
        ? true
        : template.match.kind === "stage"
          ? existsIn(name, stages)
          : existsIn(name, resources);
      spans.push({ ...base, kind: template.match.kind, value: name, known: exists, draft });
      continue;
    }

    if (readsAsVariable(match[1] ?? "", text[end] ?? "")) {
      spans.push({ ...base, kind: "unknown", value: "", known: false, draft: false });
    }
  }
  return spans;
}

/** The text in pieces, the citations wrapped, for the layer that paints them. */
export function segments(
  text: string,
  spans: VariableSpan[],
): { text: string; span?: VariableSpan }[] {
  const parts: { text: string; span?: VariableSpan }[] = [];
  let at = 0;
  for (const span of spans) {
    if (span.start > at) parts.push({ text: text.slice(at, span.start) });
    parts.push({ text: span.raw, span });
    at = span.end;
  }
  if (at < text.length) parts.push({ text: text.slice(at) });
  return parts;
}

/**
 * The citations that behave as one piece: a caret may not come to rest inside
 * them and an edit may not cut one in half. A draft is excluded, since it is a
 * placeholder the writer still has to replace.
 */
export function atomicSpans(spans: VariableSpan[]): VariableSpan[] {
  return spans.filter((span) => span.known && !span.draft);
}

/** The citation a caret at `position` may not sit inside, if there is one. */
export function spanInside(
  spans: VariableSpan[],
  position: number,
): VariableSpan | undefined {
  return spans.find((span) => span.start < position && position < span.end);
}

/** Where a caret inside a citation comes to rest: its nearer edge. */
export function nearestEdge(span: VariableSpan, position: number): number {
  return position - span.start <= span.end - position ? span.start : span.end;
}

/** A selection grown to cover whole citations, so an edit never cuts one in half. */
export function expandToWhole(
  spans: VariableSpan[],
  start: number,
  end: number,
): { start: number; end: number } {
  let from = start;
  let to = end;
  for (const span of spans) {
    if (span.start < end && start < span.end) {
      from = Math.min(from, span.start);
      to = Math.max(to, span.end);
    }
  }
  return { start: from, end: to };
}

/**
 * The line the caret is on, in client coordinates, read off the mirror.
 *
 * A textarea hands out no geometry for its caret, but the mirror carries the
 * same text in the same box, so a range over its text nodes answers it. That is
 * what lets the insert list sit under the line being written instead of over
 * it. Returns null when the mirror cannot say, and the caller falls back.
 */
export function caretLine(root: HTMLElement, offset: number): { top: number; bottom: number } | null {
  const lineHeight = Number.parseFloat(getComputedStyle(root).lineHeight) || 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let seen = 0;
  let node = walker.nextNode() as Text | null;
  while (node) {
    const length = node.data.length;
    if (seen + length >= offset) {
      const at = Math.max(0, Math.min(offset - seen, length));
      const range = document.createRange();
      range.setStart(node, at);
      range.collapse(true);
      let rect = range.getBoundingClientRect();
      if (!rect.height && at > 0) {
        // The end of a line has no box of its own. The character before it is on
        // the line just written, so one line lower is where the caret is.
        range.setStart(node, at - 1);
        const previous = range.getBoundingClientRect();
        if (previous.height) {
          const afterBreak = node.data[at - 1] === "\n";
          rect = {
            ...previous.toJSON(),
            top: afterBreak ? previous.bottom : previous.top,
            bottom: afterBreak ? previous.bottom + lineHeight : previous.bottom,
            height: afterBreak ? lineHeight : previous.height,
          } as DOMRect;
        }
      }
      return rect.height ? { top: rect.top, bottom: rect.bottom } : null;
    }
    seen += length;
    node = walker.nextNode() as Text | null;
  }
  return null;
}
