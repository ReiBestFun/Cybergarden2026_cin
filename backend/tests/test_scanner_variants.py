"""Scan unfamiliar source layouts and Kotlin DSL forms without building the project."""

from hashlib import sha256

import pytest

from backend.app.analyzers import Snapshot, scan_sast, scan_sca, scan_secrets
from backend.app.integrations.archives import prepare_archive
from backend.tests.test_analyzers import SHA, catalog
from backend.tests.test_archive_source import zipped
from backend.app.worker import run_scan


def test_archive_without_catalog_uses_worker_advisories():
    data = zipped("example/build.gradle.kts", 'dependencies { implementation("net.minidev:json-smart:2.4.8") }')
    with prepare_archive(data, sha256(data).hexdigest(), None) as source:
        result = run_scan("sca", source.snapshot, source.catalog_path, 20)
    assert result.status == "succeeded"
    assert {f.metadata["artifact"] for f in result.result.findings} == {"json-smart"}


@pytest.mark.parametrize("declaration", [
    'implementation(name = "asset", group = groupId, version = release)',
    'implementation("$groupId:asset") { version { strictly(release) } }',
])
def test_sca_named_arguments_and_strict_versions(declaration):
    build = 'val groupId = "demo"\nval release = "1.4"\ndependencies { ' + declaration + ' }'
    result = scan_sca(Snapshot(SHA, {"sub/build.gradle.kts": build}), catalog())
    assert [(f.line, f.metadata["version"]) for f in result.findings] == [(3, "1.4")]
    locked = scan_sca(Snapshot(SHA, {"sub/build.gradle.kts": build, "sub/gradle.lockfile": "demo:asset:1.7=runtimeClasspath"}), catalog())
    assert not locked.findings


def test_secrets_follow_encoded_literal_to_authorization_header():
    data = '''
        class Transport {
            fun configure(headers: HttpHeaders) {
                val envelope = "c3ludGhldGljLXRlc3QtdmFsdWU="
                val auth = Base64.getDecoder().decode(envelope).decodeToString()
                headers.setBearerAuth(auth)
            }
        }
    '''
    findings = scan_secrets(Snapshot(SHA, {"Demo.kt": data})).findings
    assert [(f.line, f.metadata["class"], f.evidence) for f in findings] == [(4, "recoverable-token", "***")]
    dynamic = data.replace('"c3ludGhldGljLXRlc3QtdmFsdWU="', 'System.getenv("SERVICE_TOKEN")')
    assert not scan_secrets(Snapshot(SHA, {"Demo.kt": dynamic})).findings


def test_tls_reports_only_a_permissive_manager_passed_to_ssl_context():
    source = '''
        class Client {
            val manager = object : X509TrustManager {
                override fun checkServerTrusted(chain: Array<X509Certificate>, type: String) {}
            }
            fun configure() {
                val ctx = SSLContext.getInstance("TLS")
                ctx.init(null, arrayOf<TrustManager>(manager), null)
            }
        }
    '''
    def rules(data):
        return [f.rule_id for f in scan_sast(Snapshot(SHA, {"Client.kt": data})).findings]

    assert rules(source) == ["sast.tls-verification"]
    assert rules(source.replace("String) {}", "String) { delegate.checkServerTrusted(chain, type) }")) == []
    assert rules(source.replace("arrayOf<TrustManager>(manager)", "null")) == []


def test_public_matcher_shadows_specific_role_only_for_the_same_method():
    data = '''
        @RestController
        class Feed {
            @GetMapping("/feed/private")
            fun privateFeed() = 1
        }
        class Security {
            fun config(http: HttpSecurity) {
                http.authorizeHttpRequests { rules ->
                    rules.requestMatchers(GET, "/feed/**").permitAll()
                    rules.requestMatchers(GET, "/feed/private").hasRole("STAFF")
                    rules.anyRequest().denyAll()
                }
            }
        }
    '''
    def rules(source):
        return [f.rule_id for f in scan_sast(Snapshot(SHA, {"Feed.kt": source})).findings]
    assert rules(data) == ["sast.route-authorization"]
    assert rules(data.replace('GET, "/feed/**"', 'POST, "/feed/**"')) == []


def test_spring_mvc_cors_requires_credentials_and_private_matching_route():
    data = '''
        class Policy : WebMvcConfigurer {
            override fun addCorsMappings(routes: CorsRegistry) {
                routes.addMapping("/profile").allowedOriginPatterns("https://*.example.test")
                    .allowCredentials(true).allowedMethods("GET")
            }
        }
        class Security {
            fun config(http: HttpSecurity) { http.cors(withDefaults()) }
        }
        @RestController
        class Profile {
            @GetMapping("/profile")
            fun profile(principal: Principal) = principal.name
        }
    '''
    def rules(source):
        return [f.rule_id for f in scan_sast(Snapshot(SHA, {"Policy.kt": source})).findings]

    assert rules(data) == ["sast.credentialed-cors"]
    assert rules(data.replace("*.example.test", "app.example.test")) == []
    assert rules(data.replace("allowCredentials(true)", "allowCredentials(false)")) == []
    assert rules(data.replace('allowedMethods("GET")', 'allowedMethods("POST")')) == []


def test_write_uses_path_id_for_table_normally_scoped_to_current_user():
    data = '''
        @RestController
        class Items(private val jdbc: NamedParameterJdbcTemplate) {
            @GetMapping("/items")
            fun mine(p: Principal) = jdbc.queryForList("SELECT id FROM item WHERE owner_id = :me", mapOf("me" to p.name))
            @DeleteMapping("/items/{id}")
            fun remove(@PathVariable id: Long) = jdbc.update("DELETE FROM item WHERE id = :key", mapOf("key" to id))
        }
    '''
    def rules(source):
        return [f.rule_id for f in scan_sast(Snapshot(SHA, {"Items.kt": source})).findings]

    assert rules(data) == ["sast.object-authorization"]
    assert rules(data.replace("DELETE FROM item", "DELETE FROM other")) == []
    assert rules(data.replace('"key" to id', '"key" to 1')) == []
    assert rules(data.replace("fun remove", '@PreAuthorize("admin") fun remove')) == []
    secured = data.replace("id: Long)", "id: Long, p: Principal)")
    assert rules(secured.replace('id = :key"', 'id = :key AND owner_id = :me"')
                 .replace('"key" to id', '"key" to id, "me" to p.name')) == []
