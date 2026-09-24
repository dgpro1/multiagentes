// Text search ignores case, accents and apostrophes: "gomez", "GOMEZ" and "Gómez" are
// the same word, and "obrien" finds "O’Brien". The server does the same for the
// lists it searches (apps/api/app/services/text_search.py).

const APOSTROPHES = /['’‘ʼ´`]/g;

/** The comparable form of a text: lowercase, without accents or apostrophes. */
export function fold(value: string | null | undefined): string {
  return (value ?? "").normalize("NFKD").replace(/\p{M}/gu, "").replace(APOSTROPHES, "").toLowerCase();
}

/** True when `haystack` contains `needle`, ignoring case, accents and apostrophes. */
export function foldIncludes(haystack: string | null | undefined, needle: string | null | undefined): boolean {
  return fold(haystack).includes(fold(needle));
}
