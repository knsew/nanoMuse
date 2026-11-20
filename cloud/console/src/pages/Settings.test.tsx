import type { ReactNode } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LangContext } from "../lib/i18n";
import type { RuntimeSettings } from "../lib/types";
import { SettingsPage } from "./Settings";

const replies = vi.hoisted(() => new Map<string, unknown>());
vi.mock("../lib/hooks", () => ({
  useData: (url: string) => ({ data: replies.get(url), reload: () => {} }), invalidate: () => {},
}));
vi.mock("./common", () => ({ Loaded: ({ q, children }: { q: { data: unknown }; children: (data: unknown) => ReactNode }) => q.data === undefined ? null : children(q.data) }));
const runtime: RuntimeSettings = {
  values: { allowance_cny: 10, signup_open: true },
  env: { allowance_cny: 10, signup_open: true },
  overridden: { allowance_cny: false, signup_open: false }, below_allowance: 0,
};
let screen: ReactTestRenderer | undefined;
const page = () => <LangContext.Provider value={{ lang: "en", setLang: () => {} }}><SettingsPage /></LangContext.Provider>;
const input = (value: string) => screen!.root.findAllByType("input").find((node) => node.props.value === value);
beforeEach(() => { replies.clear(); });
afterEach(() => { if (screen) act(() => screen!.unmount()); screen = undefined; });

describe("settings polling while editing", () => {
  it("preserves the allowance draft through an unchanged poll and accepts a changed server value", async () => {
    replies.set("/v1/admin/settings", structuredClone(runtime));
    await act(async () => { screen = create(page()); });
    act(() => { input("10")!.props.onChange({ target: { value: "42" } }); });
    replies.set("/v1/admin/settings", structuredClone(runtime));
    await act(async () => { screen!.update(page()); });
    expect(input("42")).toBeDefined();
    replies.set("/v1/admin/settings", { ...runtime, values: { ...runtime.values, allowance_cny: 20 } });
    await act(async () => { screen!.update(page()); });
    expect(input("20")).toBeDefined();
  });
});
