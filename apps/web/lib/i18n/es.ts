import type { Dictionary } from "./en";
import { core } from "./dicts/core";
import { home } from "./dicts/home";
import { clients } from "./dicts/clients";
import { agents } from "./dicts/agents";
import { channels } from "./dicts/channels";
import { social } from "./dicts/social";
import { settings } from "./dicts/settings";
import { playground } from "./dicts/playground";
import { portal } from "./dicts/portal";
import { inbox } from "./dicts/inbox";
import { tools } from "./dicts/tools";
import { reports } from "./dicts/reports";
import { calendar } from "./dicts/calendar";
import { oauth } from "./dicts/oauth";
import { pipeline } from "./dicts/pipeline";
import { professionals } from "./dicts/professionals";
import { services } from "./dicts/services";
import { lead } from "./dicts/lead";

// Spanish dictionary. Composed from the same modules; typed as Dictionary so it
// must mirror the exact shape of `en` (each area's `es` mirrors its own `en`).
export const es: Dictionary = {
  ...core.es,
  home: home.es,
  clients: clients.es,
  agents: agents.es,
  channels: channels.es,
  social: social.es,
  settings: settings.es,
  playground: playground.es,
  portal: portal.es,
  inbox: inbox.es,
  tools: tools.es,
  reports: reports.es,
  calendar: calendar.es,
  oauth: oauth.es,
  pipeline: pipeline.es,
  professionals: professionals.es,
  services: services.es,
  lead: lead.es,
};
