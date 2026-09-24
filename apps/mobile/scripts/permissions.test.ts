import assert from "node:assert/strict";
import { test } from "node:test";
import type { Session } from "../src/api";
import { hasFeature, hasPermission } from "../src/permissions";

test("current server grants control access and a role name never overrides them", () => {
  const session = { role: "admin", permissions: ["reports.view"] } as Session;
  assert.equal(hasPermission(session, "reports.view"), true);
  assert.equal(hasPermission(session, "teams.manage"), false);
  assert.equal(hasPermission({ ...session, permissions: [] }, "reports.view"), false);
  assert.equal(hasPermission({ ...session, permissions: undefined }, "reports.view"), false);
});

test("a session without a features list keeps today's behaviour", () => {
  const session = { permissions: ["reports.view", "teams.manage", "contacts.manage"] } as Session;
  for (const key of ["inbox", "contacts", "teams", "reports", "tags", "templates", "canned"] as const) assert.equal(hasFeature(session, key), true);
  assert.equal(hasPermission(session, "reports.view"), true);
  assert.equal(hasPermission(session, "teams.manage"), true);
});

test("a features list is authoritative and a grant needs its function to be on", () => {
  const session = { permissions: ["reports.view", "teams.manage", "contacts.manage", "inbox.reply"], features: ["inbox", "teams"] } as Session;
  assert.equal(hasFeature(session, "teams"), true);
  assert.equal(hasFeature(session, "contacts"), false);
  assert.equal(hasFeature(session, "reports"), false);
  assert.equal(hasFeature({ features: [] } as unknown as Session, "inbox"), false);
  assert.equal(hasPermission(session, "teams.manage"), true);
  assert.equal(hasPermission(session, "reports.view"), false);
  assert.equal(hasPermission(session, "contacts.manage"), false);
  assert.equal(hasPermission(session, "inbox.reply"), true);
});
