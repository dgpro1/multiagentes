"use client";

import { useState } from "react";
import { Compass, LoaderCircle, MapPin, X } from "lucide-react";
import { useT } from "@/lib/i18n";

/** Inline form under the inbox composer to send a WhatsApp pin: coordinates
 * (prefilled from the browser when allowed), an optional place name and
 * address. Latitude/longitude validate locally; the API re-validates. */
export function LocationComposer({ busy, disabled, onSend, onCancel }: {
  busy: boolean;
  disabled: boolean;
  onSend: (place: { latitude: number; longitude: number; name: string; address: string }) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [locating, setLocating] = useState(false);
  const [locatingError, setLocatingError] = useState("");

  const lat = Number(latitude.replace(",", "."));
  const lng = Number(longitude.replace(",", "."));
  const valid = Number.isFinite(lat) && lat >= -90 && lat <= 90 && Number.isFinite(lng) && lng >= -180 && lng <= 180;

  function useMine() {
    if (!navigator.geolocation) {
      setLocatingError(t("inbox.locationNoGeolocation"));
      return;
    }
    setLocating(true);
    setLocatingError("");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLatitude(position.coords.latitude.toFixed(6));
        setLongitude(position.coords.longitude.toFixed(6));
        setLocating(false);
      },
      () => {
        setLocating(false);
        setLocatingError(t("inbox.locationDenied"));
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  function submit() {
    if (!valid || disabled || busy) return;
    onSend({ latitude: lat, longitude: lng, name: name.trim(), address: address.trim() });
  }

  return (
    <div className="location-composer">
      <div className="location-composer-row">
        <label>{t("inbox.locationLat")}<input value={latitude} inputMode="decimal" placeholder="4.609710" onChange={(e) => setLatitude(e.target.value)} disabled={disabled} /></label>
        <label>{t("inbox.locationLng")}<input value={longitude} inputMode="decimal" placeholder="-74.081750" onChange={(e) => setLongitude(e.target.value)} disabled={disabled} /></label>
        <button type="button" className="button quiet" onClick={useMine} disabled={disabled || locating} title={t("inbox.locationUseMine")}>
          {locating ? <LoaderCircle className="spin" size={15} /> : <Compass size={15} />} {t("inbox.locationUseMine")}
        </button>
        <button type="button" className="icon-button" onClick={onCancel} aria-label={t("common.cancel")} title={t("common.cancel")}><X size={15} /></button>
      </div>
      <div className="location-composer-row">
        <label>{t("inbox.locationName")}<input value={name} maxLength={200} placeholder={t("inbox.locationNamePlaceholder")} onChange={(e) => setName(e.target.value)} disabled={disabled} /></label>
        <label>{t("inbox.locationAddress")}<input value={address} maxLength={300} placeholder={t("inbox.locationAddressPlaceholder")} onChange={(e) => setAddress(e.target.value)} disabled={disabled} /></label>
        <button type="button" className="button primary" onClick={submit} disabled={!valid || disabled || busy}>
          <MapPin size={15} /> {t("inbox.locationSendButton")}
        </button>
      </div>
      {locatingError && <small className="location-error">{locatingError}</small>}
    </div>
  );
}
