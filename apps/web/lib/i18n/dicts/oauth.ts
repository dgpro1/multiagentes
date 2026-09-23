// UI strings for the OAuth consent screen: a third party asking to act.
const en = {
  loading: "Checking the request…",
  invalidTitle: "This request is not valid",
  invalidCopy: "Ask the application to start the connection again.",
  heading: "Allow access?",
  intro: "{name} wants to operate through the API.",
  scopesTitle: "It will be able to",
  approve: "Allow access",
  deny: "Deny",
  working: "Answering…",
};

const es: typeof en = {
  loading: "Revisando la solicitud…",
  invalidTitle: "Esta solicitud no es válida",
  invalidCopy: "Pide a la aplicación que inicie la conexión de nuevo.",
  heading: "¿Permitir acceso?",
  intro: "{name} quiere operar por API.",
  scopesTitle: "Podrá",
  approve: "Permitir acceso",
  deny: "Denegar",
  working: "Respondiendo…",
};

export const oauth = { en, es };
