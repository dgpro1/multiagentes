// UI strings for the "channels" area. Fill `en` and mirror it in `es`.
const en = {
  head: {
    eyebrow: "Channels",
    title: "Channels",
    description: "Connect each client's number to its own agent and Inbox. Every connection belongs to a single client: its number, agent, session and conversations stay separate from other spaces.",
  },
  toolbar: {
    clientLabel: "Client",
    allClients: "All clients",
    openClient: "Open client",
  },
  whatsappCloud: {
    status: "Available",
    title: "WhatsApp API",
    description:
      "Official WhatsApp Business Cloud API, hosted by Meta. Connect a number with your own Meta app credentials.",
    ownerPlaceholder: "Choose a client to configure its number",
    configure: "Configure",
    selectClient: "Select a client",
  },
  whatsapp: {
    status: "Great for testing",
    title: "WhatsApp QR",
    description:
      "Scan a QR with the WhatsApp app on your phone and reply with an agent in minutes. Free and instant to set up, so it is ideal for demos and testing. For production, use WhatsApp API instead.",
    ownerPlaceholder: "Choose a client to configure its number",
    configure: "Configure",
    selectClient: "Select a client",
  },
  webchat: {
    status: "Available",
    title: "Webchat",
    description:
      "Embed an assistant on any website with a single line of code. Its conversations land in the same Inbox as WhatsApp.",
    ownerPlaceholder: "Choose a client to configure its widget",
    configure: "Configure",
    selectClient: "Select a client",
  },
  // The banner on a client's Channels tab.
  banner: {
    title: "Connected channels & visibility",
    live: "Live",
    unlinked: "Unlinked",
    active: "Active channels",
    description: "Real-time connection status and visibility for {name}.",
  },
  // Setting a number of lines: the labels are shared by the platform's Plan tab
  // (an agency's quota) and the agency's Channels tab (a client's share of it).
  quota: {
    fewer: "One less",
    more: "One more",
    unlimited: "No limit",
    setLimit: "Set a limit",
    inUseOf: "{used} in use of {quota}",
    inUseFree: "{used} in use, no limit",
    overLimit: "{used} in use of {quota} — over the limit",
    hint: "Nothing already connected is ever removed; only new lines are refused.",
    linesTitle: "Lines",
    linesCopy: "How many lines of each type this client may connect, out of the agency's plan.",
    notIncluded: "Not included in the plan",
    matrixTitle: "Lines per client",
    matrixCopy: "Every client against every channel type: what each one uses, and how many lines it was assigned out of the agency's plan.",
    planLabel: "Plan",
    usedLabel: "In use",
    matrixEmpty: "Create a client to start handing out lines.",
  },
  visibility: {
    shown: "Visibility switched on in the portal",
    hidden: "Visibility hidden in the portal",
  },
};

const es: typeof en = {
  head: {
    eyebrow: "Canales",
    title: "Canales",
    description: "Conecta el número de cada cliente con su propio agente e Inbox. Cada conexión pertenece a un solo cliente: su número, agente, sesión y conversaciones permanecen separados de los demás espacios.",
  },
  toolbar: {
    clientLabel: "Cliente",
    allClients: "Todos los clientes",
    openClient: "Abrir cliente",
  },
  whatsappCloud: {
    status: "Disponible",
    title: "WhatsApp API",
    description:
      "API oficial de WhatsApp Business Cloud, alojada por Meta. Conecta un número con las credenciales de tu propia app de Meta.",
    ownerPlaceholder: "Elige un cliente para configurar su número",
    configure: "Configurar",
    selectClient: "Selecciona un cliente",
  },
  whatsapp: {
    status: "Ideal para pruebas",
    title: "WhatsApp QR",
    description:
      "Escanea un QR con la app de WhatsApp de tu teléfono y responde con un agente en minutos. Se configura gratis y al instante, así que es ideal para demos y pruebas. Para producción usa mejor WhatsApp API.",
    ownerPlaceholder: "Elige un cliente para configurar su número",
    configure: "Configurar",
    selectClient: "Selecciona un cliente",
  },
  webchat: {
    status: "Disponible",
    title: "Chat web",
    description:
      "Inserta un asistente en cualquier sitio web con una línea de código. Sus conversaciones llegan al mismo Inbox que WhatsApp.",
    ownerPlaceholder: "Elige un cliente para configurar su widget",
    configure: "Configurar",
    selectClient: "Selecciona un cliente",
  },
  banner: {
    title: "Canales conectados y visibilidad",
    live: "En vivo",
    unlinked: "Sin vincular",
    active: "Canales activos",
    description: "Estado de conexión y visibilidad en tiempo real para {name}.",
  },
  quota: {
    fewer: "Una menos",
    more: "Una más",
    unlimited: "Sin límite",
    setLimit: "Poner un límite",
    inUseOf: "{used} en uso de {quota}",
    inUseFree: "{used} en uso, sin límite",
    overLimit: "{used} en uso de {quota} — por encima del límite",
    hint: "Nada de lo ya conectado se quita nunca; solo se rechazan las líneas nuevas.",
    linesTitle: "Líneas",
    linesCopy: "Cuántas líneas de cada tipo puede conectar este cliente, del plan de la agencia.",
    notIncluded: "No está incluido en el plan",
    matrixTitle: "Líneas por cliente",
    matrixCopy: "Cada cliente contra cada tipo de canal: qué usa y cuántas líneas tiene asignadas del plan de la agencia.",
    planLabel: "Plan",
    usedLabel: "En uso",
    matrixEmpty: "Crea un cliente para empezar a repartir líneas.",
  },
  visibility: {
    shown: "Visibilidad activada en el portal",
    hidden: "Visibilidad oculta en el portal",
  },
};

export const channels = { en, es };
