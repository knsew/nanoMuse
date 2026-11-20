package io.github.nanomuse.cloud

import com.openminis.app.data.model.ProviderCredential
import com.openminis.app.data.model.ProviderInstance
import com.openminis.app.data.model.ProviderType
import io.github.nanomuse.net.OpenCodeSession
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Contract C11 on the phone: the shipped catalogue parses, the card's order, which vendor a configured provider is, and what it covers. */
class ProviderCatalogueTest {
    /** The asset as shipped (the module directory is the working directory of a unit test). */
    private val shipped: List<CatalogueProvider> by lazy {
        val f = listOf("src/main/assets/nanomuse/providers.json", "app/src/main/assets/nanomuse/providers.json").map(::File).first { it.exists() }
        ProviderCatalogue.parse(f.readText())
    }

    private fun inst(type: ProviderType, base: String? = null, cred: ProviderCredential = ProviderCredential.apiKey) =
        ProviderInstance(id = "i-${type.name}-${base ?: ""}", label = type.displayName, providerType = type, credentialType = cred, customBaseURL = base)

    /** A synthetic catalogue entry: the template and capability rules are the same whatever the catalogue ships. */
    private fun vendor(
        id: String,
        nameZh: String = "",
        protocol: String = "openai",
        baseUrl: String = "https://$id.example/v1",
        baseUrlGlobal: String? = null,
        auth: List<String> = listOf("key"),
        authCapabilities: Map<String, Set<String>> = emptyMap(),
        capabilities: Set<String> = setOf("chat"),
    ) = CatalogueProvider(
        id = id,
        name = id,
        nameZh = nameZh,
        protocol = protocol,
        baseUrl = baseUrl,
        baseUrlGlobal = baseUrlGlobal,
        keyUrl = "",
        keyUrlGlobal = null,
        keyHint = null,
        auth = auth,
        authCapabilities = authCapabilities,
        regions = setOf("global"),
        capabilities = capabilities,
        userCapabilities = false,
        defaults = emptyMap(),
        note = "",
        noteZh = "",
        verified = "2026-10",
    )

    @Test fun `the shipped catalogue parses with the local servers and the blank entry only`() {
        assertEquals(listOf("ollama", "lm-studio", "vllm", "custom"), shipped.map { it.id })
        assertTrue(shipped.first { it.id == "custom" }.userCapabilities)
        assertTrue(shipped.first { it.id == "ollama" }.local)
        assertTrue(shipped.first { it.id == "lm-studio" }.local)
        assertTrue(shipped.first { it.id == "vllm" }.local)
        assertFalse(shipped.first { it.id == "custom" }.local)
        assertEquals("qwen3:8b", shipped.first { it.id == "ollama" }.defaults["chat"])
        for (p in shipped) assertTrue(p.id, p.verified.matches(Regex("\\d{4}-\\d{2}")))
    }

    @Test fun `a malformed entry is skipped, not the whole list`() {
        val list = ProviderCatalogue.parse(
            """{"providers":[{"name":"no id"},{"id":"x","name":"X","protocol":"openai","base_url":"https://x.example/v1","auth":["key"],"regions":["global"],"capabilities":["chat"]}]}""",
        )
        assertEquals(listOf("x"), list.map { it.id })
        assertEquals(setOf("x.example"), list.single().hosts())
    }

    @Test fun `a vendor that wants a session id per conversation is on a host that gets one`() {
        // the slimmed catalogue names no session header: no entry to check against
        assertTrue(shipped.none { it.sessionHeader != null })
        assertTrue(OpenCodeSession.wants("https://opencode.ai/zen/go/v1"))
        assertFalse(OpenCodeSession.wants("https://api.deepseek.com/v1"))
        assertFalse(OpenCodeSession.wants("https://notopencode.ai/v1"))
        assertFalse(OpenCodeSession.wants(null))
    }

    @Test fun `the card's order - the slimmed catalogue lists no cloud vendor`() {
        // local servers and the blank entry are not ways on from a spent allowance, and
        // the slimmed catalogue holds nothing else: the card's order is empty either way
        assertEquals(emptyList<String>(), ProviderCatalogue.ordered(shipped, mainland = true).map { it.id })
        assertEquals(emptyList<String>(), ProviderCatalogue.ordered(shipped, mainland = false).map { it.id })
        assertTrue(ProviderCatalogue.signIns(shipped, false).isEmpty())
        assertTrue(ProviderCatalogue.signIns(shipped, true).isEmpty())
    }

    @Test fun `which vendor a configured provider is - a local server by its address, anything else nobody's`() {
        // the local servers, by their whole address
        assertEquals("ollama", ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "http://127.0.0.1:11434/v1"))?.id)
        assertEquals("lm-studio", ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "http://127.0.0.1:1234/v1"))?.id)
        assertEquals("vllm", ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "http://127.0.0.1:8000/v1"))?.id)
        // a cloud vendor's host is nobody's now: the catalogue no longer lists it
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "https://dashscope.aliyuncs.com/compatible-mode")))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "https://api.moonshot.ai/v1")))
        // upstream's own types are nobody's either: no entry carries their id
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openRouter)))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.anthropic)))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.gemini)))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.xAI)))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.kimiCode, cred = ProviderCredential.oauth)))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openAI)))
        // a relay of one's own, nanoMuse Cloud: not in the catalogue, not gated
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "https://relay.example.org/v1")))
        assertNull(ProviderCatalogue.match(shipped, inst(ProviderType.openAI, "http://192.168.1.20:11434/v1")))
    }

    @Test fun `what a provider covers - the key's capabilities, or the sign-in's when that gives less`() {
        val openai = vendor(
            id = "openai",
            auth = listOf("key", ProviderCatalogue.AUTH_CHATGPT),
            authCapabilities = mapOf(ProviderCatalogue.AUTH_CHATGPT to setOf("chat", "vision")),
            capabilities = setOf("chat", "vision", "image"),
        )
        val chatgpt = inst(ProviderType.openAI, cred = ProviderCredential.oauth)
        assertEquals(ProviderCatalogue.AUTH_CHATGPT, ProviderCatalogue.authOf(chatgpt, openai))
        assertEquals(setOf("chat", "vision"), openai.capabilitiesFor(ProviderCatalogue.authOf(chatgpt, openai)))
        // the same vendor with a key draws
        val keyed = inst(ProviderType.openAI)
        assertNull(ProviderCatalogue.authOf(keyed, openai))
        assertTrue("image" in openai.capabilitiesFor(null))
        // a vendor with no image models
        assertFalse("image" in vendor(id = "plain", capabilities = setOf("chat")).capabilities)
        // OpenRouter's sign-in gives what the key gives
        val or = vendor(id = "openrouter", auth = listOf("key", ProviderCatalogue.AUTH_OPENROUTER))
        assertEquals(or.capabilities, or.capabilitiesFor(ProviderCatalogue.AUTH_OPENROUTER))
    }

    @Test fun `the pre-filled form - endpoint split into base and the v1 toggle, the type per vendor, the sign-in's type`() {
        val vendors = listOf(
            vendor("bailian", nameZh = "阿里云百炼", baseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            vendor("openrouter", baseUrl = "https://openrouter.ai/api/v1", auth = listOf("key", "oauth-openrouter")),
            vendor("zhipu", baseUrl = "https://open.bigmodel.cn/api/paas/v4"),
            vendor("groq", baseUrl = "https://api.groq.com/openai/v1"),
            vendor("xai", baseUrl = "https://api.x.ai/v1"),
            vendor("anthropic", protocol = "anthropic", baseUrl = "https://api.anthropic.com", auth = listOf("key", "oauth-claude")),
            vendor("gemini", protocol = "gemini", baseUrl = "https://generativelanguage.googleapis.com/v1beta/openai"),
            vendor("moonshot", baseUrl = "https://api.moonshot.cn/v1", baseUrlGlobal = "https://api.moonshot.ai/v1", auth = listOf("key", "device-kimi")),
            vendor("openai", baseUrl = "https://api.openai.com/v1", auth = listOf("key", "oauth-chatgpt")),
        )
        fun t(id: String, mainland: Boolean = true, signIn: Boolean = false) = OwnKeyPresets.template(vendors.first { it.id == id }, mainland, chinese = false, signIn = signIn)
        t("bailian").let { assertEquals(ProviderType.openAI, it.providerType); assertEquals("https://dashscope.aliyuncs.com/compatible-mode", it.baseURL); assertTrue(it.appendV1) }
        t("openrouter").let { assertEquals(ProviderType.openRouter, it.providerType); assertEquals("https://openrouter.ai/api", it.baseURL); assertTrue(it.appendV1) }
        t("zhipu").let { assertEquals("https://open.bigmodel.cn/api/paas/v4", it.baseURL); assertFalse(it.appendV1) }
        t("groq").let { assertEquals("https://api.groq.com/openai", it.baseURL); assertTrue(it.appendV1) }
        t("xai").let { assertEquals(ProviderType.xAI, it.providerType); assertEquals("https://api.x.ai", it.baseURL) }
        t("anthropic").let { assertEquals(ProviderType.anthropic, it.providerType); assertEquals("https://api.anthropic.com", it.baseURL); assertTrue(it.appendV1) }
        t("gemini").let { assertEquals(ProviderType.gemini, it.providerType); assertEquals("https://generativelanguage.googleapis.com/v1beta", it.baseURL); assertFalse(it.appendV1) }
        // the region picks the edition
        assertEquals("https://api.moonshot.cn", t("moonshot").baseURL)
        assertEquals("https://api.moonshot.ai", t("moonshot", mainland = false).baseURL)
        // a sign-in: Kimi's runs under upstream's device-code type, the others under their own
        assertEquals(ProviderType.kimiCode, t("moonshot", signIn = true).providerType)
        assertEquals(ProviderType.openAI, t("openai", signIn = true).providerType)
        assertEquals(ProviderType.anthropic, t("anthropic", signIn = true).providerType)
        // the Chinese name when the UI is Chinese
        assertEquals("阿里云百炼", OwnKeyPresets.template(vendors.first { it.id == "bailian" }, true, chinese = true).name)
    }

    @Test fun `the preset value - id and the sign-in suffix, the deep link`() {
        assertEquals("openai", OwnKeyPresets.id("openai:oauth"))
        assertEquals("bailian", OwnKeyPresets.id(" Bailian "))
        assertNull(OwnKeyPresets.id(""))
        assertTrue(OwnKeyPresets.wantsSignIn("openai:oauth"))
        assertFalse(OwnKeyPresets.wantsSignIn("openai"))
        assertEquals("minis://settings/providers/add?preset=openrouter:oauth", OwnKeyPresets.deepLink("openrouter", signIn = true))
        assertEquals("minis://settings/providers/add?preset=bailian", OwnKeyPresets.deepLink("bailian"))
        assertNotNull(ProviderCatalogue.host("https://api.openai.com/v1"))
        assertNull(ProviderCatalogue.host("not a url"))
    }
}
