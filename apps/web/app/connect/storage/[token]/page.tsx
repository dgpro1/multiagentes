"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, HardDrive, LoaderCircle, XCircle } from "lucide-react";
import { StorageConnectForm } from "@/components/storage-connect-form";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { StorageConnectInfo, StorageConnectPayload } from "@/types";

/** The public link a business owner opens to connect their own Cloudflare R2
 * bucket. No OpenLivery account: everything here rides on the link's token. */
export default function StorageConnectPage() {
  const t = useT();
  const { token } = useParams<{ token: string }>();
  const [info, setInfo] = useState<StorageConnectInfo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api<StorageConnectInfo>(`/storage/connect/${token}`)
      .then(setInfo)
      .catch((err) => { if (err instanceof ApiError && err.status === 404) setNotFound(true); });
  }, [token]);

  async function connect(payload: StorageConnectPayload) {
    setInfo(await api<StorageConnectInfo>(`/storage/connect/${token}`, { method: "POST", body: JSON.stringify(payload) }));
    setDone(true);
  }

  if (notFound) return <Shell>
    <XCircle size={40} className="connect-icon danger" />
    <h1>{t("resources.publicLink.invalidTitle")}</h1>
    <p>{t("resources.publicLink.invalidCopy")}</p>
  </Shell>;

  if (!info) return <Shell><LoaderCircle size={32} className="connect-icon spin" /></Shell>;

  if (done || info.status === "connected") return <Shell>
    <CheckCircle2 size={40} className="connect-icon" />
    <h1>{t("resources.publicLink.doneTitle")}</h1>
    <p>{t("resources.publicLink.doneCopy", { bucket: info.bucket })}</p>
  </Shell>;

  return <Shell wide>
    <HardDrive size={36} className="connect-icon" />
    <h1>{t("resources.publicLink.title")}</h1>
    <p>{t("resources.publicLink.intro", { agency: info.agency_name || "HunterAI", client: info.client_name })}</p>
    <div style={{ width: "100%", textAlign: "left" }}><StorageConnectForm onConnect={connect} /></div>
  </Shell>;
}

function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  const t = useT();
  return <div className="connect-page">
    <div className="connect-card" style={wide ? { width: "min(640px, 100%)" } : undefined}>{children}</div>
    <small className="connect-footer">{t("resources.publicLink.poweredBy")}</small>
  </div>;
}
