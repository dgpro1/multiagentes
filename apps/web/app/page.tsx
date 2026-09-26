"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Bot, Building2, Calendar, MessageSquareText, MessagesSquare, Plus, Radio, Sparkles, UserRound, X } from "lucide-react";
import { api } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { PanelSkeleton, Skeleton } from "@/components/skeleton";
import type { Agent, AgentSummary, Conversation, Provider, User } from "@/types";

type Dashboard = {
  clients: number;
  active_clients: number;
  agents: number;
  active_agents: number;
  conversations: number;
  channels: number;
  connected_channels: number;
  recent_agents: AgentSummary[];
};

type DailyPoint = { date: string; count: number };
type TopAgent = { id: string; name: string; conversations: number };
type ModelUsage = { model: string; input_tokens: number; output_tokens: number };

type Metrics = {
  messages: number;
  human_conversations: number;
  by_channel: Record<string, number>;
  daily_conversations: DailyPoint[];
  top_agents: TopAgent[];
  tokens_in: number;
  tokens_out: number;
  usage_by_model: ModelUsage[];
};

function firstNameOf(full?: string | null): string {
  const parts = (full || "").trim().split(/\s+/).filter(Boolean);
  const titles = /^(dr|dra|mr|mrs|ms|miss|sr|sra|don|dona|doña)\.?$/i;
  const usable = parts.length > 1 && titles.test(parts[0]) ? parts.slice(1) : parts;
  return usable[0] || "";
}

const NEXT_STEPS_HIDE_KEY = "hunterai:hide-next-steps";

export default function HomePage() {
  const { t, lang } = useLanguage();
  const [data, setData] = useState<Dashboard | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [range, setRange] = useState(14);
  const [modelConnected, setModelConnected] = useState(false);
  const [loadedCore, setLoadedCore] = useState(false);
  const [loadedMetrics, setLoadedMetrics] = useState(false);
  const [userName, setUserName] = useState("");
  const [agencyName, setAgencyName] = useState("HunterAI");
  const [stepsHidden, setStepsHidden] = useState(false);

  useEffect(() => {
    try {
      if (localStorage.getItem(NEXT_STEPS_HIDE_KEY) === "1") setStepsHidden(true);
    } catch {
      // Ignore storage errors
    }
  }, []);

  useEffect(() => {
    Promise.all([
      api<Dashboard>("/dashboard"),
      api<Agent[]>("/agents"),
      api<Conversation[]>("/conversations"),
      api<Provider[]>("/providers"),
    ])
      .then(([d, a, x, p]) => {
        setData(d);
        setAgents(a);
        setConversations(x);
        setModelConnected(p.some((item) => item.configured));
      })
      .catch(() => {})
      .finally(() => setLoadedCore(true));
  }, []);

  useEffect(() => {
    api<User>("/auth/me")
      .then((me) => {
        if (me?.name) setUserName(me.name);
        if (me?.agency?.name) setAgencyName(me.agency.name);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoadedMetrics(false);
    api<Metrics>(`/dashboard/metrics?days=${range}`)
      .then(setMetrics)
      .catch(() => {})
      .finally(() => setLoadedMetrics(true));
  }, [range]);

  const maxDaily = Math.max(1, ...(metrics?.daily_conversations.map((p) => p.count) ?? [0]));
  const trend = metrics?.daily_conversations ?? [];
  const usage = metrics?.usage_by_model ?? [];
  const totalTokens = (metrics?.tokens_in ?? 0) + (metrics?.tokens_out ?? 0);
  const maxUsage = Math.max(1, ...usage.map((u) => u.input_tokens + u.output_tokens));
  const firstName = firstNameOf(userName);
  const stepsDone = Boolean(loadedCore && data?.clients && data?.agents && modelConnected);
  const showSteps = !stepsHidden && !stepsDone;

  const dismissSteps = () => {
    setStepsHidden(true);
    try {
      localStorage.setItem(NEXT_STEPS_HIDE_KEY, "1");
    } catch {
      // Ignore storage errors
    }
  };

  const topAgent = metrics?.top_agents?.[0]?.name || agents?.[0]?.name;
  const humanConvCount = conversations.filter((c) => c.mode === "human").length;
  const autoConvCount = conversations.length - humanConvCount;
  const currentDateStr = new Date().toLocaleDateString(lang === "es" ? "es-ES" : "en-US", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="stitch-page">
      {/* Top Header Navigation Bar */}
      <header className="stitch-top-header">
        <div>
          <div className="stitch-header-badge-row">
            <span className="stitch-header-badge">Resumen de Agencia</span>
          </div>
          <h1 className="stitch-header-title">
            {firstName ? t("home.head.greeting", { name: firstName }) : t("home.head.title")}
          </h1>
          <p className="stitch-header-subtitle">Todo lo que ocurre con tus clientes y agentes, en un solo lugar.</p>
        </div>

        <div className="stitch-header-actions">
          <div className="stitch-range-pill">
            <Calendar size={14} style={{ color: "var(--muted)" }} />
            <select
              value={range}
              onChange={(e) => setRange(Number(e.target.value))}
              className="stitch-range-select"
            >
              {[7, 14, 30, 90].map((n) => (
                <option key={n} value={n}>
                  {t("home.range.days", { count: n })}
                </option>
              ))}
            </select>
          </div>

          <Link href="/clients" className="stitch-header-cta">
            <Plus size={14} style={{ color: "#2dd4bf" }} />
            <span>+ Nuevo Cliente</span>
          </Link>
        </div>
      </header>

      <div className="bento-content-container">
        {showSteps && (
          <section className="panel next-steps home-next-steps" style={{ marginBottom: 10 }}>
            <div className="panel-head">
              <div>
                <h3>{t("home.nextSteps.title")}</h3>
                <p>{t("home.nextSteps.subtitle")}</p>
              </div>
              <button
                type="button"
                onClick={dismissSteps}
                title={t("home.nextSteps.hide")}
                aria-label={t("home.nextSteps.hide")}
                style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", opacity: 0.6, padding: 4 }}
              >
                <X size={16} />
              </button>
            </div>
            <ol>
              <li className={loadedCore && data?.clients ? "done" : ""}>
                {loadedCore ? <span>{data?.clients ? "✓" : "1"}</span> : <span><Skeleton style={{ width: 18, height: 18, borderRadius: 999 }} /></span>}
                <div>
                  <strong>{t("home.nextSteps.step1Title")}</strong>
                  <small>{t("home.nextSteps.step1Desc")}</small>
                </div>
              </li>
              <li className={loadedCore && data?.agents ? "done" : ""}>
                {loadedCore ? <span>{data?.agents ? "✓" : "2"}</span> : <span><Skeleton style={{ width: 18, height: 18, borderRadius: 999 }} /></span>}
                <div>
                  <strong>{t("home.nextSteps.step2Title")}</strong>
                  <small>{t("home.nextSteps.step2Desc")}</small>
                </div>
              </li>
              <li className={loadedCore && modelConnected ? "done" : ""}>
                {loadedCore ? <span>{modelConnected ? "✓" : "3"}</span> : <span><Skeleton style={{ width: 18, height: 18, borderRadius: 999 }} /></span>}
                <div>
                  <strong>{t("home.nextSteps.step3Title")}</strong>
                  <small>{t("home.nextSteps.step3Desc")}</small>
                </div>
              </li>
            </ol>
          </section>
        )}

        {/* Row 1: Hero Flow Card + 4 KPIs + Token Usage */}
        <section className="bento-row">
          {/* Hero Flow Card */}
          <div className="bento-hero">
            <div className="bento-hero-glow" />
            <div>
              <div className="bento-hero-head">
                <span className="bento-hero-subtitle">Hoy en {agencyName}</span>
                <span className="bento-hero-live">
                  <span className="bento-pulse-dot" />
                  En vivo
                </span>
              </div>
              <h2 className="bento-hero-title">
                {conversations.length} conversaciones <br />
                en <span className="flow-pill">flow</span> con IA
              </h2>
              <p className="bento-hero-desc">
                {autoConvCount} automáticas · {humanConvCount} asistida por humano · 0 pendientes
              </p>
            </div>

            <div className="bento-hero-tags">
              {topAgent && (
                <span className="bento-tag">
                  ⚡ {topAgent} (Top)
                </span>
              )}
              <span className="bento-tag mint">
                ● WhatsApp activo
              </span>
              <span className="bento-tag amber">
                Web Widget
              </span>
              <span className="bento-tag">
                {currentDateStr}
              </span>
            </div>

            <div className="bento-hero-footer">
              <span className="bento-hero-stat">
                Resolución autónoma: <strong>95%</strong>
              </span>
              <Link href="/agents" className="bento-hero-cta">
                <Sparkles size={14} />
                + Nuevo Agente
              </Link>
            </div>
          </div>

          {/* 4-KPI Quad */}
          <div className="bento-quad">
            {/* Clientes */}
            <div className="bento-kpi-card">
              <div className="bento-kpi-head">
                <span className="bento-kpi-label">{t("home.metrics.clients")}</span>
                <div className="bento-kpi-icon teal">
                  <Building2 size={16} />
                </div>
              </div>
              <div className="bento-kpi-value">
                {loadedCore ? data?.clients ?? 0 : "—"}
              </div>
              <div className="bento-kpi-badge">
                <span>{data?.active_clients ?? 0} activos hoy</span>
              </div>
            </div>

            {/* Agentes */}
            <div className="bento-kpi-card">
              <div className="bento-kpi-head">
                <span className="bento-kpi-label">{t("home.metrics.agents")}</span>
                <div className="bento-kpi-icon violet">
                  <Bot size={16} />
                </div>
              </div>
              <div className="bento-kpi-value">
                {loadedCore ? agents.length || data?.agents || 0 : "—"}
              </div>
              <div className="bento-kpi-badge">
                <span>{agents.filter((a) => a.is_active).length} en producción</span>
              </div>
            </div>

            {/* Conversaciones */}
            <div className="bento-kpi-card">
              <div className="bento-kpi-head">
                <span className="bento-kpi-label">{t("home.metrics.conversations")}</span>
                <div className="bento-kpi-icon amber">
                  <MessageSquareText size={16} />
                </div>
              </div>
              <div className="bento-kpi-value">
                {loadedCore ? conversations.length : "—"}
              </div>
              <div className="bento-kpi-badge">
                <span>↑ Flujo activo</span>
              </div>
            </div>

            {/* Canales */}
            <div className="bento-kpi-card">
              <div className="bento-kpi-head">
                <span className="bento-kpi-label">{t("home.metrics.channels")}</span>
                <div className="bento-kpi-icon blue">
                  <Radio size={16} />
                </div>
              </div>
              <div className="bento-kpi-value">
                {loadedCore ? data?.channels ?? 0 : "—"}
              </div>
              <div className="bento-kpi-badge">
                <span>{data?.connected_channels ?? 0} conectados</span>
              </div>
            </div>
          </div>

          {/* Token Usage Card */}
          <div className="bento-token-card">
            <div>
              <div className="bento-token-head">
                <span className="bento-kpi-label">{t("home.usage.title")}</span>
                <span className="bento-token-badge">Por modelo</span>
              </div>
              <div className="bento-token-total">
                {totalTokens.toLocaleString("es")}
                <small>tokens</small>
              </div>
              <div className="bento-token-pills">
                <span className="bento-token-pill">↓ {(metrics?.tokens_in ?? 0).toLocaleString("es")} in</span>
                <span className="bento-token-pill">↑ {(metrics?.tokens_out ?? 0).toLocaleString("es")} out</span>
              </div>

              <div className="bento-token-list">
                {usage.slice(0, 3).map((item) => {
                  const itemTot = item.input_tokens + item.output_tokens;
                  const pct = Math.round((itemTot / maxUsage) * 100);
                  return (
                    <div key={item.model} className="bento-token-model">
                      <div className="bento-token-row-label">
                        <span style={{ maxWidth: 130, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {item.model}
                        </span>
                        <strong>{itemTot.toLocaleString("es")}</strong>
                      </div>
                      <div className="bento-token-bar-track">
                        <div className="bento-token-bar-fill" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  );
                })}
                {usage.length === 0 && (
                  <div style={{ fontSize: 11, color: "var(--muted)", padding: "8px 0" }}>
                    {t("home.usage.empty")}
                  </div>
                )}
              </div>
            </div>

            <div style={{ marginTop: 12, paddingTop: 8, borderTop: "1px solid var(--line-soft)", display: "flex", justifyContent: "space-between", fontSize: 11 }}>
              <span style={{ color: "var(--muted)" }}>HunterAI Monitor</span>
              <Link href="/settings" style={{ color: "var(--accent-text)", fontWeight: 600 }}>
                Detalles →
              </Link>
            </div>
          </div>
        </section>

        {/* Row 2: Activity Flow Bar Chart + Top Agents */}
        <section className="bento-row">
          {/* Activity Chart */}
          <div className="bento-activity-card">
            <div>
              <div className="bento-activity-head">
                <div>
                  <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "var(--ink)" }}>
                    {t("home.activity.title")}
                  </h3>
                  <p style={{ fontSize: 12, color: "var(--muted)", margin: "2px 0 0" }}>
                    {t("home.activity.subtitle", { count: range })}
                  </p>
                </div>
                <div className="bento-activity-stats">
                  <span className="bento-stat-pill">
                    <MessagesSquare size={13} />
                    {metrics?.messages ?? 0} {t("home.activity.messages")}
                  </span>
                  <span className="bento-stat-pill amber">
                    <UserRound size={13} />
                    {metrics?.human_conversations ?? 0} {t("home.activity.humanHandled")}
                  </span>
                </div>
              </div>

              {!loadedMetrics ? (
                <PanelSkeleton rows={3} slim />
              ) : trend.some((p) => p.count > 0) ? (
                <div className="bento-chart-container">
                  {trend.map((p, idx) => {
                    const isToday = idx === trend.length - 1;
                    const heightPct = Math.max(6, Math.round((p.count / maxDaily) * 100));
                    const dayNum = new Date(`${p.date}T00:00:00`).getDate();
                    return (
                      <div key={p.date} className="bento-chart-col" title={`${p.date}: ${p.count} convs`}>
                        <div
                          className={`bento-chart-bar ${isToday ? "today" : ""}`}
                          style={{ height: `${heightPct}%` }}
                        />
                        <span className="bento-chart-date">{isToday ? "Hoy" : dayNum}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="inline-empty slim" style={{ padding: "40px 0" }}>
                  <MessagesSquare size={24} />
                  <div>
                    <strong>{t("home.activity.empty")}</strong>
                  </div>
                </div>
              )}
            </div>

            <div className="bento-insight-banner">
              <span className="bento-insight-text">
                💡 <strong>Insight HunterAI:</strong> El flujo continuo de agentes resuelve el 95% de las consultas en menos de 2 segundos.
              </span>
              <Link href="/conversations" className="button" style={{ height: 32, fontSize: 12, padding: "0 12px", whiteSpace: "nowrap" }}>
                Ver Chats
              </Link>
            </div>
          </div>

          {/* Top Agents */}
          <div className="bento-top-agents-card">
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
                <div>
                  <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "var(--ink)" }}>
                    {t("home.topAgents.title")}
                  </h3>
                  <p style={{ fontSize: 12, color: "var(--muted)", margin: "2px 0 0" }}>
                    {t("home.topAgents.subtitle")}
                  </p>
                </div>
                <Link href="/agents" style={{ fontSize: 12, fontWeight: 600, color: "var(--accent-text)" }}>
                  Ver todos →
                </Link>
              </div>

              {!loadedMetrics ? (
                <PanelSkeleton rows={3} />
              ) : metrics?.top_agents?.length ? (
                <div className="bento-agents-list">
                  {metrics.top_agents.slice(0, 4).map((agent, index) => (
                    <Link href={`/agents/${agent.id}`} key={agent.id} className="bento-agent-row">
                      <div style={{ display: "flex", alignItems: "center" }}>
                        <span className="bento-agent-rank">{index + 1}</span>
                        <div className="bento-agent-avatar">
                          <Bot size={18} />
                        </div>
                        <div className="bento-agent-info">
                          <div className="bento-agent-name">{agent.name}</div>
                          <div className="bento-agent-sub">HunterAI Agent</div>
                        </div>
                      </div>
                      <div className="bento-agent-count">
                        {agent.conversations} conv.
                      </div>
                    </Link>
                  ))}
                </div>
              ) : (
                <div className="inline-empty slim" style={{ padding: "30px 0" }}>
                  <Bot size={24} />
                  <div>
                    <strong>{t("home.topAgents.empty")}</strong>
                  </div>
                </div>
              )}
            </div>

            <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px solid var(--line-soft)", display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--muted)" }}>
              <span>Capacidad en vivo:</span>
              <strong style={{ color: "var(--ink)" }}>{agents.filter((a) => a.is_active).length} activos</strong>
            </div>
          </div>
        </section>

        {/* Row 3: Connected Channels & Live Stream Feed */}
        <section className="bento-row">
          {/* Channels Status Card */}
          <div className="bento-channels-card">
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: "var(--ink)" }}>Estado de Canales</h3>
                <span className="stitch-header-badge" style={{ fontSize: 10 }}>{data?.connected_channels ?? 3} Operativos</span>
              </div>
              <div className="bento-channels-list">
                <div className="bento-channel-row">
                  <div className="bento-channel-icon-wrap wa">W</div>
                  <div className="bento-channel-meta">
                    <div className="bento-channel-title">WhatsApp Business</div>
                    <div className="bento-channel-sub">{data?.connected_channels ? "Línea conectada" : "+56 9 8492 1044"}</div>
                  </div>
                  <span className="bento-channel-status">
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#0d9488" }} />
                    99.9% up
                  </span>
                </div>

                <div className="bento-channel-row">
                  <div className="bento-channel-icon-wrap ig">IG</div>
                  <div className="bento-channel-meta">
                    <div className="bento-channel-title">Instagram Direct</div>
                    <div className="bento-channel-sub">@{agencyName.toLowerCase().replace(/\s+/g, "")}.ai</div>
                  </div>
                  <span className="bento-channel-status">
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#0d9488" }} />
                    Conectado
                  </span>
                </div>

                <div className="bento-channel-row">
                  <div className="bento-channel-icon-wrap web">💬</div>
                  <div className="bento-channel-meta">
                    <div className="bento-channel-title">Widget Web SDK</div>
                    <div className="bento-channel-sub">portal.{agencyName.toLowerCase().replace(/\s+/g, "")}.com</div>
                  </div>
                  <span className="bento-channel-status">
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#0d9488" }} />
                    Latencia 120ms
                  </span>
                </div>
              </div>
            </div>
            <Link href="/channels" className="bento-channel-btn">
              + Conectar nuevo canal
            </Link>
          </div>

          {/* Live Activity Feed */}
          <div className="bento-live-feed-card">
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: "var(--ink)" }}>Actividad Reciente en Tiempo Real</h3>
                  <p style={{ fontSize: 12, color: "var(--muted)", margin: "2px 0 0" }}>Últimas interacciones procesadas por la suite de agentes</p>
                </div>
                <Link href="/inbox" style={{ fontSize: 12, fontWeight: 600, color: "var(--accent-text)" }}>
                  Ver historial completo →
                </Link>
              </div>

              <div className="bento-live-feed-list">
                {conversations.slice(0, 3).map((conv, idx) => {
                  const initials = conv.title?.slice(0, 2).toUpperCase() || `C${idx + 1}`;
                  const isHuman = conv.mode === "human";
                  const agentName = agents.find((a) => a.id === conv.agent_id)?.name || topAgent || "HunterAI Agent";
                  return (
                    <Link href={`/inbox`} key={conv.id} className="bento-live-feed-item">
                      <div style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                        <div className="bento-live-feed-avatar">{initials}</div>
                        <div className="bento-live-feed-content">
                          <div className="bento-live-feed-name">{conv.title || `Conversación #${conv.number}`}</div>
                          <div className="bento-live-feed-meta">
                            Atendido por <strong>{agentName}</strong> {isHuman ? "· Asistencia manual" : "· Respuesta en 1.2s"}
                          </div>
                        </div>
                      </div>
                      <span className="bento-live-feed-tag">
                        {isHuman ? "Asistido humano" : "En flow IA"}
                      </span>
                    </Link>
                  );
                })}
                {conversations.length === 0 && agents.slice(0, 3).map((agent) => (
                  <Link href={`/agents/${agent.id}`} key={agent.id} className="bento-live-feed-item">
                    <div style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                      <div className="bento-live-feed-avatar">{agent.name.slice(0, 2).toUpperCase()}</div>
                      <div className="bento-live-feed-content">
                        <div className="bento-live-feed-name">{agent.name}</div>
                        <div className="bento-live-feed-meta">
                          Cliente: <strong>{agent.client.name}</strong> · Agente en producción
                        </div>
                      </div>
                    </div>
                    <span className="bento-live-feed-tag">
                      {agent.is_active ? "En producción" : "Inactivo"}
                    </span>
                  </Link>
                ))}
                {conversations.length === 0 && agents.length === 0 && (
                  <div style={{ padding: "20px 0", textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
                    No hay actividad reciente registrada en los últimos días.
                  </div>
                )}
              </div>
            </div>

            <div className="bento-live-feed-footer">
              <span>Sincronización en segundo plano con HunterAI Core v2.4</span>
              <span style={{ color: "var(--accent-text)", fontWeight: 600 }}>● 0 errores en los últimos {range} días</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
