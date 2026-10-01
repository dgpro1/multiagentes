/**
 * The name the agency login shows above the form.
 *
 * Every agency signs in at the same address, so the page cannot know whose it
 * is until someone has signed in. The panel remembers the agency's name in
 * this browser each time it loads a session, and the login shows it from then
 * on; a browser that never signed in shows the product name.
 */
const KEY = "openlivery.login-agency-name";
export const DEFAULT_LOGIN_NAME = "HunterAI";

export function rememberAgencyName(name: string): void {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    // Storage can be unavailable (private mode, blocked site data).
  }
}

export function rememberedAgencyName(): string | null {
  try {
    return window.localStorage.getItem(KEY)?.trim() || null;
  } catch {
    return null;
  }
}
