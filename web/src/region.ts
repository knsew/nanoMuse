/**
 * Where the person is, for the "ways on" copy (contracts C5 and C11): the own-key way
 * leads to a server of one's own now (vLLM on the network, the same in every region),
 * and the facts about it (key page, endpoint, default models, what the key covers) come
 * from the catalogue `nanomuse/llm/providers.json`, the one file every client reads.
 *
 * Mainland = the UI language is 简体中文, or the account was signed in with a phone
 * number (codes reach mainland numbers only), or the relay says `region: "cn"`. A relay
 * that does not say yet is fine: the other two signs still work.
 */
import catalogue from "../../nanomuse/llm/providers.json";
import { getLocale } from "./i18n";
import type { Capability, CatalogueProvider, CloudAccount, ProviderPreset } from "./types";

export function isMainland(account?: Pick<CloudAccount, "channel" | "region" | "signed_in"> | null): boolean {
  if (account?.region) return account.region === "cn";
  if (account?.signed_in && account.channel === "sms") return true;
  return getLocale() === "zh-CN";
}

/** A number with a country code that is not mainland China's: no text message can reach it. */
export function looksLikeForeignNumber(value: string): boolean {
  // 86 is the only country code that starts with 86, spaced or not: "+8613800138000" is
  // as mainland as "+86 138 0000 0000", which the word boundary alone never matched
  return /^\s*(\+|00)(?!86)\d/.test(value);
}

export interface OwnKeyWay {
  /** the catalogue id: "vllm" */
  id: "vllm";
  /** the preset id in Connections: "halogen", the self-hosted server on the local network */
  preset: "halogen";
  label: string;
  keyUrl: string;
  baseUrl: string;
  chatModel: string;
  guiModel: string;
  /** what one key there covers (contract C11) */
  covers: Capability[];
}

const BY_ID = new Map((catalogue as { providers: CatalogueProvider[] }).providers.map((p) => [p.id, p]));

function wayFrom(id: "vllm", preset: "halogen"): OwnKeyWay {
  const p = BY_ID.get(id);
  if (!p) throw new Error(`providers.json has no ${id}`);
  return {
    id,
    preset,
    label: p.name,
    keyUrl: p.key_url,
    baseUrl: p.base_url,
    chatModel: p.defaults.chat ?? "",
    guiModel: p.defaults.hands ?? p.defaults.chat ?? "",
    covers: p.capabilities,
  };
}

/** The own-key provider to lead with: the local vLLM server, the same in every region. */
const VLLM = wayFrom("vllm", "halogen");

/** The own-key provider to lead with for this person. */
export function ownKeyWay(): OwnKeyWay {
  return VLLM;
}

/**
 * The one sentence under "Use your own model key" (contracts C5 and C11): a server of
 * one's own, and what it answers. English first, then 简体中文 through the dictionary.
 */
export function ownKeyLine(t: (s: string) => string): string {
  return t("Use a server of your own: a vLLM server on your network answers chat, and the hands too when the served model sees pictures.");
}

/** Whether a model id says it sees pictures (the runtime's rule, mirrored for the pickers). */
export function modelSees(id: string): boolean | null {
  const name = (id || "").toLowerCase().split("/").pop() ?? "";
  if (!name) return null;
  if (name.startsWith("deepseek")) return /v4\.1|vision|ocr/.test(name);
  if (name.startsWith("qwen")) return name.includes("-vl") || name.startsWith("qwen3.8") ? true : null;
  return null;
}

/** The presets in the order to show them: the region's own-key provider first. */
export function orderPresets(ids: string[], presets: Record<string, ProviderPreset>, account?: Pick<CloudAccount, "channel" | "region" | "signed_in"> | null): string[] {
  const first = ownKeyWay().preset;
  if (!ids.includes(first)) return ids;
  const mainland = isMainland(account);
  // outside the mainland, the mainland-only vendor goes last in its group
  const rest = ids.filter((id) => id !== first);
  if (mainland) return [first, ...rest];
  const cn = rest.filter((id) => presets[id]?.region === "cn");
  return [first, ...rest.filter((id) => !cn.includes(id)), ...cn];
}
