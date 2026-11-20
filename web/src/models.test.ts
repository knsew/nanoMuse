import { describe, expect, it } from "vitest";
import { CLOUD_ID, currentChoice, endpointChoice, endpointGroups, endpointLabel, endpointOf, entryFor, handsValue, mediaChoices, providerLabel, slotValue } from "./models";
import type { CustomEndpoint, MediaSlotData } from "./types";

const OLLAMA = "http://127.0.0.1:11434/v1";
const VLLM = "http://127.0.0.1:8000/v1";
/** a host the catalogue does not list */
const ELSEWHERE = "https://elsewhere.example/v1";

function slot(over: Partial<MediaSlotData>): MediaSlotData {
  return {
    provider: "",
    provider_id: "",
    model: "",
    base_url: "",
    key_source: "none",
    configured: false,
    from_app: false,
    effective_provider: "",
    effective_model: "",
    effective_source: "",
    ...over,
  };
}

describe("the model slots (the Models contract)", () => {
  it("finds the catalogue entry a slot stands on, by id or by host", () => {
    expect(entryFor("ollama", "")?.id).toBe("ollama");
    expect(entryFor("vllm", "")?.id).toBe("vllm");
    expect(entryFor("lm-studio", "")?.id).toBe("lm-studio");
    expect(entryFor("custom", "")?.id).toBe("custom");
    expect(entryFor("openai", OLLAMA)?.id).toBe("ollama");
    // the match is by host only, so every loopback server reads as the first one
    expect(entryFor("openai", VLLM)?.id).toBe("ollama");
    // a deleted cloud provider no longer resolves, by id or by host
    expect(entryFor("bailian", "")).toBeNull();
    expect(entryFor("openai", "https://dashscope.aliyuncs.com/compatible-mode/v1")).toBeNull();
    expect(entryFor("openai", "https://api.openai.com/v1")).toBeNull();
    expect(entryFor("openai", "https://gateway.example.com/v1")).toBeNull();
    expect(entryFor("openai", "https://gateway.example.ai/v1")).toBeNull();
    expect(entryFor("openai", "https://gateway.example.dev/v1")).toBeNull();
    expect(entryFor("openai", "https://eu.openrouter.ai/api/v1")).toBeNull();
    expect(entryFor("openai", "https://synap402.com/v1")).toBeNull();
    expect(entryFor("openai", "")).toBeNull();
  });

  it("writes a row's value as provider · model", () => {
    expect(slotValue(slot({ effective_provider: CLOUD_ID, effective_model: "qwen-image-3.0" }), "en")).toBe("nanoMuse Cloud · qwen-image-3.0");
    expect(slotValue(slot({ effective_provider: "ollama", effective_model: "qwen3-vl" }), "zh-CN")).toBe("Ollama（本机） · qwen3-vl");
    expect(slotValue(slot({ effective_provider: "ollama", effective_model: "qwen3-vl" }), "en")).toBe("Ollama · qwen3-vl");
    expect(slotValue(slot({ effective_provider: "custom", effective_model: "flux-2", base_url: "https://images.example.com/v1" }), "en")).toBe(
      "images.example.com · flux-2",
    );
    expect(slotValue(slot({}), "en")).toBe("");
    expect(providerLabel("nope", "en")).toBe("nope");
  });

  it("offers only own-key providers with the capability", () => {
    // the local servers cover chat only, so no chat provider joins a media picker
    const chat = { provider: "openai", base_url: OLLAMA, cloud: false };
    const video = mediaChoices("video", { signedIn: true, chat, region: "cn", locale: "en" });
    expect(video.map((c) => [c.value, c.group, c.needsKey])).toEqual([["custom", "add", false]]);
    // signed out, a chat provider the catalogue does not list: a host of one's own only
    const elsewhere = { provider: "openai", base_url: ELSEWHERE, cloud: false };
    const image = mediaChoices("image", { signedIn: false, chat: elsewhere, region: "global", locale: "en" });
    expect(image.find((c) => c.value === CLOUD_ID)).toBeUndefined();
    expect(image.find((c) => c.group === "chat")).toBeUndefined();
    expect(image.filter((c) => c.group === "add" && c.entry).every((c) => c.entry?.capabilities.includes("image"))).toBe(true);
    expect(image.map((c) => c.value)).toEqual(["custom"]);
    // the relay as the chat model is not an own provider; nothing in the chat group
    const cloud = mediaChoices("video", { signedIn: true, chat: { provider: "openai", base_url: "https://relay.example/v1", cloud: true }, region: "global", locale: "en" });
    expect(cloud.map((c) => c.value)).toEqual(["custom"]);
  });

  it("writes the hands row's value from the effective fields, so Automatic shows what it resolves to", () => {
    const gui = { provider: "openai", provider_id: "", base_url: "" };
    const ollama = { provider: "openai", base_url: OLLAMA, cloud: false };
    // the chat provider's own hands model
    expect(handsValue({ ...gui, effective_model: "qwen3-vl", effective_source: "chat" }, ollama, "en")).toBe("Ollama · qwen3-vl");
    expect(handsValue({ ...gui, effective_model: "qwen3-vl", effective_source: "chat" }, ollama, "zh-CN")).toBe("Ollama（本机） · qwen3-vl");
    // the account's, while the chat model is elsewhere or is the relay
    const elsewhere = { provider: "openai", base_url: ELSEWHERE, cloud: false };
    expect(handsValue({ ...gui, effective_model: "qwen3-vl", effective_source: "cloud" }, elsewhere, "en")).toBe("nanoMuse Cloud · qwen3-vl");
    expect(handsValue({ ...gui, effective_model: "qwen3-vl", effective_source: "cloud" }, { provider: "openai", base_url: "https://relay.example/v1", cloud: true }, "en")).toBe(
      "nanoMuse Cloud · qwen3-vl",
    );
    // a model picked by hand: on the chat endpoint, on a catalogue provider of its own, on an unlisted host
    expect(handsValue({ ...gui, effective_model: "qwen3-vl", effective_source: "gui" }, { provider: "ollama", base_url: "", cloud: false }, "en")).toBe("Ollama · qwen3-vl");
    expect(handsValue({ provider: "vllm", provider_id: "vllm", base_url: "", effective_model: "qwen3-vl", effective_source: "gui" }, elsewhere, "en")).toBe(
      "vLLM · qwen3-vl",
    );
    expect(handsValue({ provider: "openai", provider_id: "", base_url: "https://gateway.example.com/v1", effective_model: "ui-tars", effective_source: "gui" }, elsewhere, "en")).toBe(
      "gateway.example.com · ui-tars",
    );
    expect(handsValue({ ...gui, effective_model: "", effective_source: "chat" }, elsewhere, "en")).toBe("");
    expect(handsValue({ ...gui, effective_model: "gpt-5.6", effective_source: "chat" }, { provider: "chatgpt", base_url: "", cloud: false }, "en")).toBe("ChatGPT · gpt-5.6");
  });

  it("reads the picker's value back from what the runtime reports", () => {
    expect(currentChoice(slot({}), "https://relay.example/v1")).toBe("");
    expect(currentChoice(slot({ configured: true, provider: "ollama", provider_id: "ollama" }), "")).toBe("ollama");
    expect(currentChoice(slot({ configured: true, provider: "openai", base_url: "https://relay.example/v1" }), "https://relay.example/v1")).toBe(CLOUD_ID);
    expect(currentChoice(slot({ configured: true, provider: "openai", base_url: "https://images.example.com/v1" }), "")).toBe("custom");
  });
});

describe("several endpoints of one's own (#294)", () => {
  const gateway: CustomEndpoint = {
    id: "ep-1a2b3c4d",
    label: "my gateway",
    base_url: "https://gateway.example/v1",
    key_source: "vault",
    models: ["ui-tars", "deepseek-chat", "qwen3-vl"],
    slots: ["chat"],
  };
  const local: CustomEndpoint = { id: "ep-9f9f9f9f", label: "", base_url: "http://127.0.0.1:8000/v1", key_source: "none", models: ["llava"], slots: [] };
  const endpoints = [gateway, local];

  it("names a picker value after the endpoint and reads it back", () => {
    expect(endpointChoice("ep-1a2b3c4d")).toBe("endpoint:ep-1a2b3c4d");
    expect(endpointOf("endpoint:ep-1a2b3c4d")).toBe("ep-1a2b3c4d");
    expect(endpointOf("endpoint:")).toBeNull();
    expect(endpointOf("ollama")).toBeNull();
  });

  it("reads an endpoint by its name, else by its host with the port", () => {
    expect(endpointLabel(gateway)).toBe("my gateway");
    expect(endpointLabel(local)).toBe("127.0.0.1:8000");
    expect(endpointLabel({ label: "  ", base_url: "not a url" })).toBe("not a url");
  });

  it("offers the endpoints to the media pickers between the chat provider and the catalogue", () => {
    const chat = { provider: "openai", base_url: OLLAMA, cloud: false };
    const image = mediaChoices("image", { signedIn: false, chat, region: "cn", locale: "en", endpoints });
    expect(image.map((c) => [c.value, c.label, c.group, c.needsKey])).toEqual([
      ["endpoint:ep-1a2b3c4d", "my gateway", "endpoints", false],
      ["endpoint:ep-9f9f9f9f", "127.0.0.1:8000", "endpoints", false],
      ["custom", "", "add", false],
    ]);
    expect(image[image.length - 1].value).toBe("custom");
    // a slot standing on one reads back as that endpoint, whatever its host
    expect(currentChoice(slot({ configured: true, provider: "openai", base_url: gateway.base_url, endpoint: gateway.id }), "")).toBe("endpoint:ep-1a2b3c4d");
    expect(slotValue(slot({ effective_provider: "custom", effective_model: "flux-2", base_url: gateway.base_url, endpoint: gateway.id }), "en", undefined, endpoints)).toBe(
      "my gateway · flux-2",
    );
    // without the list (an older runtime) the host stands, as before
    expect(slotValue(slot({ effective_provider: "custom", effective_model: "flux-2", base_url: gateway.base_url, endpoint: gateway.id }), "en")).toBe("gateway.example · flux-2");
  });

  it("gives the hands picker one group per other endpoint, less the models known not to see", () => {
    const groups = endpointGroups(endpoints, "ep-9f9f9f9f");
    expect(groups.map((g) => [g.key, g.label, g.rows.map((r) => r.id)])).toEqual([["endpoint:ep-1a2b3c4d", "my gateway", ["ui-tars", "qwen3-vl"]]]);
    expect(endpointGroups([{ ...gateway, models: ["deepseek-chat"] }])).toEqual([]);
  });

  it("writes the hands row's value with the endpoint's name", () => {
    const elsewhere = { provider: "openai", base_url: ELSEWHERE, cloud: false };
    const onGateway = { provider: "openai", provider_id: "", base_url: gateway.base_url, endpoint: gateway.id, effective_model: "ui-tars", effective_source: "gui" as const };
    expect(handsValue(onGateway, elsewhere, "en", undefined, endpoints)).toBe("my gateway · ui-tars");
    expect(handsValue(onGateway, elsewhere, "en")).toBe("gateway.example · ui-tars");
    // riding on a chat model that answers through an endpoint
    const chatOnLocal = { provider: "openai", base_url: local.base_url, cloud: false, endpoint: local.id };
    expect(handsValue({ provider: "openai", provider_id: "", base_url: "", effective_model: "llava", effective_source: "chat" }, chatOnLocal, "en", undefined, endpoints)).toBe(
      "127.0.0.1:8000 · llava",
    );
  });
});
