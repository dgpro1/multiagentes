"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Building2,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  Globe,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings,
} from "lucide-react";
import { api } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import { businessLabel, useIndustries } from "@/lib/industries";
import { EmptyState } from "@/components/ui";
import { TableSkeleton } from "@/components/skeleton";
import type { Agent, Client } from "@/types";
import { foldIncludes } from "@/lib/text";

export default function ClientsPage() {
  const { t, lang } = useLanguage();
  const catalog = useIndustries();

  const [clients, setClients] = useState<Client[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [industryFilter, setIndustryFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [copied, setCopied] = useState(false);

  const fetchAll = () => {
    setRefreshing(true);
    Promise.all([api<Client[]>("/clients"), api<Agent[]>("/agents")])
      .then(([cList, aList]) => {
        setClients(cList);
        setAgents(aList);
        if (!selectedId && cList.length > 0) {
          setSelectedId(cList[0].id);
        }
      })
      .catch(() => {})
      .finally(() => {
        setLoaded(true);
        setRefreshing(false);
      });
  };

  useEffect(() => {
    fetchAll();
  }, []);

  const labelOf = (item: Client) => businessLabel(catalog, item, lang);

  const visible = useMemo(() => {
    return clients.filter((item) => {
      const matchesSearch = foldIncludes(
        `${item.name} ${labelOf(item)} ${item.industry || ""}`,
        search,
      );
      const matchesIndustry = industryFilter === "all" || item.industry === industryFilter;
      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "active" && item.is_active) ||
        (statusFilter === "inactive" && !item.is_active);

      return matchesSearch && matchesIndustry && matchesStatus;
    });
  }, [clients, search, industryFilter, statusFilter, catalog, lang]);

  const selectedClient = useMemo(() => {
    if (selectedId) {
      const found = clients.find((c) => c.id === selectedId);
      if (found) return found;
    }
    return visible[0] || clients[0] || null;
  }, [clients, selectedId, visible]);

  const selectedAgent = useMemo(() => {
    if (!selectedClient) return null;
    return agents.find((a) => a.client_id === selectedClient.id) || null;
  }, [selectedClient, agents]);

  const copyPortalUrl = () => {
    if (!selectedClient) return;
    const url = selectedClient.portal_domain
      ? `https://${selectedClient.portal_domain}`
      : `${typeof window !== "undefined" ? window.location.origin : ""}/portal/${selectedClient.portal_slug}`;
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="stitch-clients-page">
      {/* Top Bar / Directory Header */}
      <header className="stitch-clients-header" data-purpose="top-header">
        <div className="stitch-clients-header-top">
          <div>
            <span className="stitch-clients-eyebrow">{t("clients.list.eyebrow")}</span>
            <h1 className="stitch-clients-title">{t("clients.list.title")}</h1>
            <p className="stitch-clients-subtitle">{t("clients.list.description")}</p>
          </div>

          <Link href="/clients/new" className="stitch-clients-cta">
            <Plus size={16} />
            <span>{t("clients.list.newClient")}</span>
          </Link>
        </div>

        {/* Filter and Search Toolbar */}
        <div className="stitch-clients-toolbar">
          {/* Search Box with ⌘K badge */}
          <div className="stitch-clients-search">
            <Search size={16} className="stitch-clients-search-icon" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("clients.list.searchPlaceholder")}
            />
            <span className="stitch-clients-search-kbd">⌘K</span>
          </div>

          {/* Quick Filters */}
          <div className="stitch-clients-filters">
            {/* Industry Filter */}
            <div className="stitch-filter-pill">
              <span style={{ color: "var(--muted)", fontSize: 11 }}>Industria:</span>
              <select
                value={industryFilter}
                onChange={(e) => setIndustryFilter(e.target.value)}
              >
                <option value="all">Todas</option>
                {catalog.map((ind) => (
                  <option key={ind.code} value={ind.code}>
                    {ind.label[lang] || ind.code}
                  </option>
                ))}
              </select>
            </div>

            {/* Status Filter */}
            <div className="stitch-filter-pill">
              <span style={{ color: "var(--muted)", fontSize: 11 }}>Estado:</span>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as "all" | "active" | "inactive")}
              >
                <option value="all">Todos</option>
                <option value="active">Activo</option>
                <option value="inactive">Inactivo / Pausa</option>
              </select>
            </div>
          </div>
        </div>
      </header>

      {/* Master-Detail Split Screen Container */}
      <div className="stitch-clients-split">
        {/* Left Column: Master Table / Directory */}
        <section className="stitch-clients-table-panel" data-purpose="clients-table-container">
          {/* Table Header Details */}
          <div className="stitch-table-head">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h2 style={{ fontSize: 14, fontWeight: 700, margin: 0, color: "var(--ink)" }}>
                Listado de Clientes
              </h2>
              <span className="stitch-header-badge" style={{ fontSize: 10, padding: "2px 8px" }}>
                {visible.length} Registros
              </span>
            </div>

            <button
              type="button"
              onClick={fetchAll}
              disabled={refreshing}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "var(--muted)",
                fontSize: 12,
                fontWeight: 500,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <RefreshCw size={13} className={refreshing ? "spin" : ""} />
              <span>Actualizar</span>
            </button>
          </div>

          {/* Table Rows Body */}
          {!loaded ? (
            <div style={{ padding: 20 }}>
              <TableSkeleton columns={4} rows={5} />
            </div>
          ) : visible.length > 0 ? (
            <div className="stitch-table-list">
              {visible.map((client) => {
                const isSelected = selectedClient?.id === client.id;
                const initials = client.name.slice(0, 2).toUpperCase();
                const subtitle = labelOf(client) || t("clients.list.industryUndefined");
                const agentCount = client.agents.length;

                return (
                  <div
                    key={client.id}
                    onClick={() => setSelectedId(client.id)}
                    className={`stitch-client-row ${isSelected ? "selected" : ""}`}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setSelectedId(client.id)}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0, flex: 1 }}>
                      {/* Avatar */}
                      <div className="stitch-client-avatar">{initials}</div>

                      {/* Info */}
                      <div className="stitch-client-info">
                        <div className="stitch-client-name">
                          <span>{client.name}</span>
                          <span
                            className="status"
                            style={{
                              fontSize: 10,
                              minHeight: 18,
                              padding: "0 6px",
                              backgroundColor: client.is_active ? "#d1fae5" : "#fef3c7",
                              color: client.is_active ? "#065f46" : "#92400e",
                            }}
                          >
                            <span
                              style={{
                                width: 5,
                                height: 5,
                                borderRadius: "50%",
                                backgroundColor: client.is_active ? "#10b981" : "#f59e0b",
                                display: "inline-block",
                                marginRight: 4,
                              }}
                            />
                            {client.is_active ? "Activo" : "En pausa"}
                          </span>
                        </div>
                        <div className="stitch-client-sub">{subtitle}</div>
                      </div>
                    </div>

                    {/* Right Meta */}
                    <div className="stitch-client-meta">
                      <div style={{ textAlign: "right" }} className="hidden sm:block">
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-2)" }}>
                          {catalog.find((i) => i.code === client.industry)?.label[lang] || client.industry || "General"}
                        </div>
                        <div style={{ fontSize: 10.5, color: "var(--muted)" }}>
                          {agentCount > 0
                            ? `${agentCount} agente${agentCount === 1 ? "" : "s"} asignado${agentCount === 1 ? "" : "s"}`
                            : "0 agentes (Borrador)"}
                        </div>
                      </div>

                      <span
                        className={`stitch-client-pill ${
                          client.portal_enabled ? "published" : "unpublished"
                        }`}
                      >
                        {client.portal_enabled
                          ? t("clients.list.portalPublished")
                          : t("clients.list.portalUnpublished")}
                      </span>

                      <div className="stitch-select-btn">
                        <ChevronRight size={16} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ padding: 40 }}>
              <EmptyState
                icon={<Building2 />}
                title={search ? t("clients.list.emptyNoMatchTitle") : t("clients.list.emptyCreateTitle")}
                description={
                  search
                    ? t("clients.list.emptyNoMatchDescription")
                    : t("clients.list.emptyCreateDescription")
                }
                action={
                  !search && (
                    <Link href="/clients/new" className="button primary">
                      <Plus size={18} /> {t("clients.list.createClient")}
                    </Link>
                  )
                }
              />
            </div>
          )}

          {/* Table Footer */}
          <div
            style={{
              padding: "12px 20px",
              borderTop: "1px solid var(--line-soft)",
              background: "var(--surface-2)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: 11.5,
              color: "var(--muted)",
            }}
          >
            <span>
              Mostrando {visible.length} de {clients.length} clientes
            </span>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                disabled
                style={{
                  padding: "4px 10px",
                  borderRadius: 8,
                  border: "1px solid #d8e6e3",
                  background: "transparent",
                  color: "var(--muted)",
                  fontSize: 11,
                  opacity: 0.5,
                  cursor: "not-allowed",
                }}
              >
                Anterior
              </button>
              <button
                style={{
                  padding: "4px 10px",
                  borderRadius: 8,
                  border: "none",
                  background: "#0f766e",
                  color: "#ffffff",
                  fontSize: 11,
                  fontWeight: 700,
                }}
              >
                1
              </button>
              <button
                disabled
                style={{
                  padding: "4px 10px",
                  borderRadius: 8,
                  border: "1px solid #d8e6e3",
                  background: "transparent",
                  color: "var(--muted)",
                  fontSize: 11,
                  opacity: 0.5,
                  cursor: "not-allowed",
                }}
              >
                Siguiente
              </button>
            </div>
          </div>
        </section>

        {/* Right Column: Inspector Detail Panel */}
        <section className="stitch-clients-detail-panel" data-purpose="client-detail-panel">
          {selectedClient ? (
            <>
              {/* Header / Client Identity */}
              <div className="stitch-detail-header">
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                    <div
                      style={{
                        width: 48,
                        height: 48,
                        borderRadius: 16,
                        background: "#0f766e",
                        color: "#ffffff",
                        fontWeight: 800,
                        fontSize: 16,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        boxShadow: "0 2px 8px rgba(15, 118, 110, 0.25)",
                        flexShrink: 0,
                      }}
                    >
                      {selectedClient.name.slice(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, color: "var(--ink)" }}>
                          {selectedClient.name}
                        </h2>
                        <span
                          style={{
                            width: 7,
                            height: 7,
                            borderRadius: "50%",
                            backgroundColor: selectedClient.is_active ? "#10b981" : "#f59e0b",
                            display: "inline-block",
                          }}
                        />
                      </div>
                      <p style={{ fontSize: 12, color: "var(--muted)", margin: "3px 0 0" }}>
                        {labelOf(selectedClient) || "Cliente HunterAI"}
                      </p>
                    </div>
                  </div>

                  <Link
                    href={`/clients/${selectedClient.id}`}
                    title="Configurar cliente"
                    style={{
                      padding: 8,
                      borderRadius: 10,
                      color: "var(--muted)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <Settings size={18} />
                  </Link>
                </div>

                {/* Portal URL Box */}
                <div className="stitch-portal-box">
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <Globe size={15} style={{ color: "#0f766e", flexShrink: 0 }} />
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: "var(--ink)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {selectedClient.portal_domain || `${selectedClient.portal_slug}.hunterai.app`}
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={copyPortalUrl}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      fontSize: 11,
                      fontWeight: 600,
                      color: "#0f766e",
                      background: "#e0f2f1",
                      border: "none",
                      padding: "4px 10px",
                      borderRadius: 8,
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    {copied ? <Check size={12} /> : <Copy size={12} />}
                    <span>{copied ? "¡Copiado!" : "Copiar"}</span>
                  </button>
                </div>
              </div>

              {/* Scrollable Body */}
              <div className="stitch-detail-body">
                {/* Quick Bento KPI Tiles */}
                <div className="stitch-detail-kpis">
                  <div className="stitch-detail-kpi-tile">
                    <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--muted)" }}>
                      Hoy
                    </span>
                    <div className="stitch-detail-kpi-val">8</div>
                    <span style={{ fontSize: 10, color: "#0f766e", fontWeight: 600 }}>Conversaciones</span>
                  </div>

                  <div className="stitch-detail-kpi-tile">
                    <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--muted)" }}>
                      Agente
                    </span>
                    <div className="stitch-detail-kpi-val" style={{ color: "#0d9488" }}>
                      {selectedClient.agents.length} Online
                    </div>
                    <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 500 }}>
                      {selectedAgent?.name || "Asignado"}
                    </span>
                  </div>

                  <div className="stitch-detail-kpi-tile">
                    <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--muted)" }}>
                      CSAT
                    </span>
                    <div className="stitch-detail-kpi-val" style={{ color: "#0d9488" }}>
                      98%
                    </div>
                    <span style={{ fontSize: 10, color: "var(--green-text)", fontWeight: 600 }}>Satisfacción</span>
                  </div>
                </div>

                {/* Assigned AI Agent Card */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                    <label style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--muted)" }}>
                      Agente Principal
                    </label>
                    {selectedAgent ? (
                      <Link
                        href={`/agents/${selectedAgent.id}`}
                        style={{ fontSize: 11, fontWeight: 600, color: "#0f766e", textDecoration: "none" }}
                      >
                        Ver configuración
                      </Link>
                    ) : (
                      <Link
                        href={`/agents/new?client_id=${selectedClient.id}`}
                        style={{ fontSize: 11, fontWeight: 600, color: "#0f766e", textDecoration: "none" }}
                      >
                        + Crear agente
                      </Link>
                    )}
                  </div>

                  <div className="stitch-detail-card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 10,
                            background: "#e0f2f1",
                            color: "#0f766e",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                          }}
                        >
                          <Bot size={18} />
                        </div>
                        <div>
                          <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)", display: "flex", alignItems: "center", gap: 6 }}>
                            {selectedAgent?.name || "Sin agente asignado"}
                            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981" }} />
                          </div>
                          <div style={{ fontSize: 11, color: "var(--muted)" }}>
                            {labelOf(selectedClient) || "Atención y reservas 24/7"}
                          </div>
                        </div>
                      </div>

                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: "#e0f2f1",
                          color: "#0f766e",
                        }}
                      >
                        Operativo
                      </span>
                    </div>

                    <p
                      style={{
                        margin: "10px 0 0",
                        padding: 10,
                        borderRadius: 10,
                        background: "var(--surface-2)",
                        border: "1px solid var(--line-soft)",
                        fontSize: 11,
                        color: "var(--ink-2)",
                        fontStyle: "italic",
                        lineHeight: 1.5,
                      }}
                    >
                      &quot;Hola, soy el asistente virtual de {selectedClient.name}. ¿Te gustaría agendar una consulta o conocer más información?&quot;
                    </p>
                  </div>
                </div>

                {/* Connected Channels Section */}
                <div>
                  <label style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--muted)", display: "block", marginBottom: 8 }}>
                    Canales Conectados
                  </label>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {/* WhatsApp */}
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "10px 12px",
                        background: "var(--surface-2)",
                        border: "1px solid var(--line-soft)",
                        borderRadius: 12,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          style={{
                            width: 26,
                            height: 26,
                            borderRadius: 8,
                            background: "#0d9488",
                            color: "#ffffff",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 11,
                            fontWeight: 800,
                          }}
                        >
                          W
                        </div>
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>WhatsApp Business</div>
                          <div style={{ fontSize: 10.5, color: "var(--muted)" }}>Línea activa de atención</div>
                        </div>
                      </div>
                      <span style={{ fontSize: 10.5, fontWeight: 600, color: "#0d9488", background: "#d1fae5", padding: "2px 8px", borderRadius: 999 }}>
                        Activo
                      </span>
                    </div>

                    {/* Web Widget */}
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "10px 12px",
                        background: "var(--surface-2)",
                        border: "1px solid var(--line-soft)",
                        borderRadius: 12,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          style={{
                            width: 26,
                            height: 26,
                            borderRadius: 8,
                            background: "#0f766e",
                            color: "#ffffff",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 11,
                          }}
                        >
                          💬
                        </div>
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>Web Chat Widget</div>
                          <div style={{ fontSize: 10.5, color: "var(--muted)" }}>Portal &amp; SDK incrustado</div>
                        </div>
                      </div>
                      <span style={{ fontSize: 10.5, fontWeight: 600, color: "#0d9488", background: "#d1fae5", padding: "2px 8px", borderRadius: 999 }}>
                        Activo
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Panel Footer Actions */}
              <div className="stitch-detail-footer">
                <Link
                  href={`/portal/${selectedClient.portal_slug}`}
                  target="_blank"
                  className="stitch-detail-open-btn"
                >
                  <ExternalLink size={14} />
                  <span>Abrir Portal del Cliente</span>
                </Link>

                <div className="stitch-detail-sub-actions">
                  <Link href={`/clients/${selectedClient.id}`} className="stitch-detail-sub-btn">
                    <Settings size={14} />
                    <span>Configurar</span>
                  </Link>

                  <Link href={`/clients/${selectedClient.id}/channels/whatsapp`} className="stitch-detail-sub-btn">
                    <Radio size={14} />
                    <span>Canales</span>
                  </Link>
                </div>
              </div>
            </>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>
              <Building2 size={32} style={{ margin: "0 auto 12px", opacity: 0.5 }} />
              <p style={{ fontSize: 13, margin: 0 }}>Selecciona un cliente para ver sus detalles</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
