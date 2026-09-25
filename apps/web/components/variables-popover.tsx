"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Search, Tag, User, Phone, Mail, Hash, Bot, Radio } from "lucide-react";
import { useT } from "@/lib/i18n";
import type { ContactValues } from "@/lib/contact-variables";

export type VariableItem = {
  key: string;
  token: string;
  label: string;
  category: string;
  icon: typeof User;
  value?: string;
};

interface VariablesPopoverProps {
  open: boolean;
  onClose: () => void;
  onSelect: (value: string) => void;
  query?: string;
  contactValues?: ContactValues | Record<string, string | null | undefined>;
  leadNumber?: number;
  dealValue?: number | string | null;
  channel?: string;
}

function fold(s: string): string {
  return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
}

export function VariablesPopover({
  open,
  onClose,
  onSelect,
  query = "",
  contactValues,
  leadNumber,
  dealValue,
  channel,
}: VariablesPopoverProps) {
  const t = useT();
  const [selectedIndex, setSelectedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  const variables: VariableItem[] = useMemo(() => [
    {
      key: "contact_name",
      token: "[contact_name]",
      label: "Nombre del contacto",
      category: "Contacto",
      icon: User,
      value: contactValues?.contact_name || undefined,
    },
    {
      key: "contact_phone",
      token: "[contact_phone]",
      label: "Teléfono",
      category: "Contacto",
      icon: Phone,
      value: contactValues?.contact_phone || undefined,
    },
    {
      key: "contact_email",
      token: "[contact_email]",
      label: "Email",
      category: "Contacto",
      icon: Mail,
      value: contactValues?.contact_email || undefined,
    },
    {
      key: "contact_company",
      token: "[company]",
      label: "Compañía",
      category: "Contacto",
      icon: User,
      value: contactValues && "company" in contactValues ? (contactValues as any).company || undefined : undefined,
    },
    {
      key: "lead_name",
      token: "[lead_name]",
      label: "Nombre del lead",
      category: "Lead",
      icon: User,
      value: contactValues?.contact_name || undefined,
    },
    {
      key: "budget",
      token: "[budget]",
      label: "Presupuesto",
      category: "Lead",
      icon: Tag,
      value: dealValue ? `$${dealValue}` : "$0",
    },
    {
      key: "lead_number",
      token: "[lead_number]",
      label: "ID del lead",
      category: "Lead",
      icon: Hash,
      value: leadNumber ? `#${leadNumber}` : undefined,
    },
  ], [contactValues, leadNumber, dealValue]);

  const filtered = useMemo(() => {
    if (!query || !query.trim()) return variables;
    const q = fold(query);
    return variables.filter((v) => {
      const label = fold(v.label);
      const key = fold(v.key);
      const cat = fold(v.category);
      const val = v.value ? fold(v.value) : "";
      return label.includes(q) || key.includes(q) || cat.includes(q) || val.includes(q);
    });
  }, [variables, query]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query, open]);

  const pick = (item: VariableItem) => {
    const val = item.value ?? "";
    onSelect(val);
    onClose();
  };

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        onClose();
      }
    }
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopPropagation();
        setSelectedIndex((prev) => (prev + 1) % (filtered.length || 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopPropagation();
        setSelectedIndex((prev) => (prev - 1 + (filtered.length || 1)) % (filtered.length || 1));
      } else if (event.key === "Enter" || event.key === "Tab") {
        if (filtered.length > 0 && filtered[selectedIndex]) {
          event.preventDefault();
          event.stopPropagation();
          pick(filtered[selectedIndex]);
        }
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleOutsideClick);
      window.addEventListener("keydown", handleKeyDown, true);
      return () => {
        document.removeEventListener("mousedown", handleOutsideClick);
        window.removeEventListener("keydown", handleKeyDown, true);
      };
    }
  }, [open, onClose, onSelect, selectedIndex, filtered]);

  if (!open) return null;

  return (
    <div ref={containerRef} className="variables-popover" role="dialog" aria-label="Variables Popover">
      <div className="variables-header">
        <div className="variables-title">
          <div>CAMPOS DEL LEAD Y</div>
          <div>CONTACTO</div>
        </div>
        <div className="variables-hint">
          <div>↑↓ NAVEGAR • ↵</div>
          <div>ELEGIR</div>
        </div>
      </div>
      <div className="variables-list" role="listbox">
        {filtered.length === 0 ? (
          <div className="variables-empty">Sin variables coincidentes</div>
        ) : (
          filtered.map((item, idx) => {
            const isSelected = idx === selectedIndex;
            return (
              <button
                key={item.key}
                type="button"
                className={`variable-item${isSelected ? " selected" : ""}`}
                onClick={() => pick(item)}
                onMouseEnter={() => setSelectedIndex(idx)}
                role="option"
                aria-selected={isSelected}
              >
                <div className="variable-item-left">
                  <span className="variable-token-name">[{item.label}]</span>
                  <span className="variable-category-pill">{item.category}</span>
                </div>
                <div className="variable-item-right">
                  {item.value ? (
                    <span className="variable-value" title={item.value}>{item.value}</span>
                  ) : (
                    <span className="variable-empty-val"><em>vacío</em></span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
