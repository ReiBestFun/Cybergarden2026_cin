"""Source-level checks for supported Kotlin and Spring security patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterator

import yaml
from tree_sitter import Node

from . import kotlin as kt
from .models import Finding, Scan, ScanResult, Severity, Snapshot, Warning
from .spring_settings import spring_settings

_RULES: dict[str, tuple[Severity, str, str, str]] = {
    "error-stacktrace": (
        "medium", "Стек исключения в ответе сервера",
        "Spring Boot настроен передавать стек исключения в ответах об ошибках.",
        "Установите server.error.include-stacktrace=never.",
    ),
    "sql-injection": (
        "high", "Пользовательский ввод в SQL", "SQL-команда строится на основе параметра запроса.",
        "Передавайте значения отдельными параметрами JDBC, а текст запроса оставляйте статическим.",
    ),
    "tls-verification": (
        "medium", "Ослабленная проверка TLS", "Клиент использует настройку, отключающую проверку TLS.",
        "Используйте штатную проверку сертификата и имени хоста.",
    ),
    "credentialed-cors": (
        "medium", "Слишком широкий CORS с учётными данными",
        "Широкий допуск источников применяется к маршруту с пользовательскими данными.",
        "Разрешайте учётные данные только для конкретных доверенных источников.",
    ),
    "route-authorization": (
        "high", "Отсутствует ограничение доступа к маршруту",
        "Публичное правило открывает обработчик, который работает с защищаемыми данными или действиями.",
        "Ограничьте доступ нужной ролью и проверьте порядок правил Spring Security.",
    ),
    "object-authorization": (
        "high", "Отсутствует проверка владельца объекта",
        "Идентификатор из запроса используется для чтения записи без подтверждённой проверки владельца.",
        "Ограничьте выборку или ответ идентификатором владельца из аутентифицированного пользователя.",
    ),
    "actuator-exposure": (
        "high", "Публичный доступ к чувствительному Actuator",
        "Конфигурация включает чувствительный management endpoint, для которого действует permitAll.",
        "Ограничьте список публикуемых endpoints и защитите служебные маршруты отдельной политикой доступа.",
    ),
}
_MAPPING_METHODS = {f"{method.title()}Mapping": method for method in ("GET", "POST", "PUT", "DELETE", "PATCH")}
_AUTH_ANNOTATIONS = {"PreAuthorize", "Secured", "RolesAllowed", "PostAuthorize"}
_WEB_INPUTS = {"RequestParam", "PathVariable", "RequestHeader", "RequestBody", "CookieValue", "ModelAttribute"}
_OWNERS = re.compile(r"\b(?:owner|customer|user|account|tenant)_?id\b", re.IGNORECASE)
_OWNER_BIND = re.compile(r"\b(?:owner|customer|user|account|tenant)_?id\s*=\s*(\?)", re.IGNORECASE)
_SENSITIVE = re.compile(
    r"\b(?:(?:customer|user|account|tenant|owner)_?(?:id|address|number)|"
    r"password|email|email_address|phone|phone_number|ssn)\b", re.IGNORECASE
)
_SQL_START = re.compile(r"^\s*(?:select|insert|update|delete|with|from)\b", re.IGNORECASE)
_JDBC_METHODS = {"query", "queryForList", "queryForObject", "queryForMap", "update", "execute"}
_JDBC_TYPES = {"JdbcTemplate", "NamedParameterJdbcTemplate", "JdbcOperations", "NamedParameterJdbcOperations"}
# APIs that execute a query passed as their first argument: (receiver types, methods).
_SQL_SINKS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset(_JDBC_TYPES), frozenset(_JDBC_METHODS)),
    (frozenset({"EntityManager", "Session", "StatelessSession"}),
     frozenset({"createQuery", "createNativeQuery", "createSQLQuery"})),
    (frozenset({"Statement"}), frozenset({"executeQuery", "executeUpdate", "executeLargeUpdate", "execute", "addBatch"})),
    (frozenset({"Connection"}), frozenset({"prepareStatement", "prepareCall", "nativeSQL"})),
    (frozenset({"JdbcClient"}), frozenset({"sql"})),
)
# Values of these types cannot carry SQL syntax, so they do not propagate injection.
_SAFE_TYPES = frozenset({
    "Int", "Long", "Short", "Byte", "Double", "Float", "Boolean", "Char", "UInt", "ULong",
    "UUID", "BigInteger", "BigDecimal", "LocalDate", "LocalDateTime", "LocalTime", "Instant",
    "OffsetDateTime", "ZonedDateTime", "Duration",
})
# Handler parameters that Spring fills from the framework, not from the HTTP request.
_FRAMEWORK_TYPES = frozenset({
    "Model", "ModelMap", "MutableMap", "Map", "BindingResult", "Errors", "WebDataBinder", "SessionStatus",
    "RedirectAttributes", "HttpServletRequest", "HttpServletResponse", "ServletRequest", "ServletResponse",
    "HttpSession", "WebRequest", "NativeWebRequest", "ServerWebExchange", "ServerHttpRequest", "ServerHttpResponse",
    "Principal", "Authentication", "Locale", "TimeZone", "ZoneId", "UriComponentsBuilder", "Pageable", "Sort",
    "InputStream", "OutputStream", "Reader", "Writer",
})
# Calls whose result type is known without type inference.
_FACTORY_TYPES = {
    "createStatement": "Statement", "createEntityManager": "EntityManager",
    "openSession": "Session", "getCurrentSession": "Session", "openStatelessSession": "StatelessSession",
    "getConnection": "Connection",
}
_SPRING_DATA_REPOSITORIES = frozenset({
    "Repository", "CrudRepository", "PagingAndSortingRepository", "JpaRepository", "ListCrudRepository",
    "ReactiveCrudRepository", "CoroutineCrudRepository",
})
_FUNCTION_SCOPE = frozenset({"function_declaration", "class_declaration"})
_SPRING_CONFIGURATION = {"application.yml", "application.yaml", "application.properties"}
_ACTUATOR_METHODS = {
    "env": "GET", "heapdump": "GET", "logfile": "GET",
    "configprops": "GET", "mappings": "GET", "threaddump": "GET", "shutdown": "POST",
}


def accepts_sast_source(path: PurePosixPath) -> bool:
    return path.suffix == ".kt" or path.name in _SPRING_CONFIGURATION


def accepts_spring_source(path: PurePosixPath) -> bool:
    return accepts_sast_source(path) or bool(re.fullmatch(r"application-.+\.(?:properties|ya?ml)", path.name))


def _short(node: Node) -> str:
    return kt.text(node).rsplit(".", 1)[-1]


def _unqualified(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _type(node: Node) -> str:
    user_type = kt.child(node, "user_type")
    if user_type is None and (nullable := kt.child(node, "nullable_type")) is not None:
        user_type = kt.child(nullable, "user_type")
    return kt.text(user_type).split("<", 1)[0] if user_type is not None else ""


def _annotations(node: Node) -> Iterator[tuple[str, Node]]:
    modifiers = kt.child(node, "modifiers")
    if modifiers is None and node.type == "parameter_modifiers":
        modifiers = node
    if modifiers is not None:
        for entry in kt.children(modifiers):
            if entry.type != "annotation":
                continue
            parts = kt.children(entry)
            if not parts:
                continue
            invocation = parts[-1]
            annotation = kt.child(invocation, "user_type") if invocation.type == "constructor_invocation" else invocation
            if annotation is not None:
                yield _short(annotation), invocation


def _literal_nodes(node: Node) -> Iterator[tuple[Node, str]]:
    for child in kt.walk(node, "string_literal"):
        if (value := kt.literal(child)) is not None:
            yield child, value


def _parameter_list(function: Node) -> dict[str, tuple[str, set[str]]]:
    result = {}
    modifiers: set[str] = set()
    params = kt.child(function, "function_value_parameters")
    if params is not None:
        for param in kt.children(params):
            if param.type == "parameter_modifiers":
                modifiers = {name for name, _ in _annotations(param)}
            elif param.type == "parameter":
                name = kt.child(param, "identifier")
                if name is not None:
                    result[kt.text(name)] = (_type(param), modifiers)
                modifiers = set()
    return result


@dataclass(frozen=True)
class Function:
    path: str
    source: kt.KotlinFile
    node: Node
    body: Node
    owner: str
    name: str
    parameters: tuple[str, ...]
    parameter_types: dict[str, str]
    inputs: frozenset[str]
    principal: frozenset[str]
    guarded: bool
    property_types: dict[str, str]
    # Parameters Spring binds from the request when the function is a handler (annotated or not).
    request_parameters: frozenset[str]
    # Parameters whose declared type cannot carry SQL syntax.
    scalar_parameters: frozenset[str]

    def receiver_type(self, receiver: Node) -> str:
        name = re.sub(r"^this\.", "", kt.text(receiver)).rstrip("!?")
        if local := self._local_type(name):
            return local
        return self.parameter_types.get(name) or self.property_types.get(name, "")

    def _local_type(self, name: str) -> str:
        """Type of a local variable: declared explicitly or produced by a known factory call."""
        for declaration in kt.walk(self.body, "property_declaration", stop=_FUNCTION_SCOPE):
            variable = kt.child(declaration, "variable_declaration")
            identifier = kt.child(variable, "identifier") if variable is not None else None
            if identifier is None or kt.text(identifier) != name:
                continue
            if declared := _type(variable):
                return declared
            value = kt.property_value(declaration)
            factory = kt.call(value[1]) if value else None
            if factory is not None and factory.name in _FACTORY_TYPES:
                return _FACTORY_TYPES[factory.name]
        return ""

    def calls(self) -> Iterator[tuple[Node, kt.Call]]:
        for node in kt.walk(self.body, "call_expression", stop=_FUNCTION_SCOPE):
            if call := kt.call(node):
                yield node, call


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    function: Function


def _request_mapping(node: Node) -> Iterator[tuple[str, str]]:
    for name, annotation in _annotations(node):
        if name not in _MAPPING_METHODS and name != "RequestMapping":
            continue
        method = _MAPPING_METHODS.get(name, "*")
        arguments = kt.child(annotation, "value_arguments")
        paths: list[str] = []
        if arguments is not None:
            for arg in kt.children(arguments):
                values = kt.children(arg)
                if len(values) == 2 and kt.text(values[0]) == "method":
                    methods = re.findall(r"\bRequestMethod\.(GET|POST|PUT|DELETE|PATCH)\b", kt.text(values[-1]))
                    if len(methods) == 1:
                        method = methods[0]
                    continue
                if len(values) == 1 or (len(values) == 2 and kt.text(values[0]) in {"value", "path"}):
                    paths.extend(value for _, value in _literal_nodes(values[-1]))
        for path in paths or [""]:
            yield method, path


def _index(
    path: str, source: kt.KotlinFile
) -> tuple[list[Function], list[Route], dict[str, list[str]]]:
    functions: list[Function] = []
    routes: list[Route] = []
    supertypes: dict[str, list[str]] = {}
    package_node = kt.child(source.root, "package_header")
    package_name = kt.child(package_node, "qualified_identifier") if package_node else None
    package = kt.text(package_name) if package_name else ""
    imports = {}
    for entry in kt.children(source.root):
        if entry.type == "import" and (qualified := kt.child(entry, "qualified_identifier")) is not None:
            alias = kt.child(entry, "identifier")
            name = kt.text(qualified)
            imports[kt.text(alias) if alias is not None else _unqualified(name)] = name

    def qualify(name: str) -> str:
        if not name or "." in name:
            return name
        return imports.get(name) or (f"{package}.{name}" if package else name)

    for declaration in kt.walk(source.root, "class_declaration"):
        class_name = declaration.child_by_field_name("name")
        class_body = kt.child(declaration, "class_body")
        if class_name is None or class_body is None:
            continue
        annotations = {name for name, _ in _annotations(declaration)}
        class_mapping = list(_request_mapping(declaration)) or [("*", "")]
        fields = {}
        for param in kt.walk(declaration, "class_parameter", stop=frozenset({"class_body"})):
            name, kind = kt.child(param, "identifier"), _type(param)
            if name is not None and kind:
                fields[kt.text(name)] = qualify(kind)
        properties = {}
        for node in kt.children(class_body):
            variable = kt.child(node, "variable_declaration") if node.type == "property_declaration" else None
            name = kt.child(variable, "identifier") if variable is not None else None
            if name is not None and (kind := _type(variable)):
                properties[kt.text(name)] = qualify(kind)
        specifiers = kt.child(declaration, "delegation_specifiers")
        if specifiers is not None:
            supertypes[qualify(kt.text(class_name))] = [
                qualify(_type(specifier)) for specifier in kt.children(specifiers) if _type(specifier)
            ]
        for node in kt.children(class_body):
            if node.type != "function_declaration":
                continue
            name, body = node.child_by_field_name("name"), kt.child(node, "function_body")
            if name is None or body is None:
                continue
            params = _parameter_list(node)
            guarded = bool(_AUTH_ANNOTATIONS & (annotations | {name for name, _ in _annotations(node)}))
            handler = bool(annotations & {"RestController", "Controller"}) and any(True for _ in _request_mapping(node))
            function = Function(
                path, source, node, body, qualify(kt.text(class_name)), kt.text(name), tuple(params),
                fields | {key: qualify(kind) for key, (kind, _) in params.items()},
                frozenset(key for key, (_, modifiers) in params.items() if modifiers & _WEB_INPUTS),
                frozenset(key for key, (kind, modifiers) in params.items()
                          if _unqualified(qualify(kind)) in {"Principal", "Authentication"}
                          or "AuthenticationPrincipal" in modifiers),
                guarded,
                fields | properties,
                frozenset(
                    key for key, (kind, modifiers) in params.items()
                    if handler and (modifiers & _WEB_INPUTS or (
                        not modifiers and _unqualified(qualify(kind)) not in _FRAMEWORK_TYPES
                    ))
                ),
                frozenset(key for key, (kind, _) in params.items() if _unqualified(kind) in _SAFE_TYPES),
            )
            functions.append(function)
            if annotations & {"RestController", "Controller"}:
                for parent_method, parent_path in class_mapping:
                    for method, subpath in _request_mapping(node):
                        route_path = "/" + "/".join(part.strip("/") for part in (parent_path, subpath) if part.strip("/"))
                        routes.append(Route(method if method != "*" else parent_method, route_path, function))
    return functions, routes, supertypes


def _located_finding(kind: str, path: str, start: int, end: int) -> Finding:
    severity, title, description, remediation = _RULES[kind]
    return Finding(
        f"sast.{kind}", "sast", severity, title, description, remediation, path,
        start, min(end, start + 14), title, {"class": kind},
    )


def _finding(kind: str, function: Function, node: Node, last: Node | None = None) -> Finding:
    return _located_finding(kind, function.path, kt.line(node), kt.end_line(last or node))


def _sql_calls(function: Function, *, all_sinks: bool = False) -> Iterator[tuple[Node, kt.Call, Node, str]]:
    """Yield query calls whose first argument is SQL text; JDBC only unless all_sinks."""
    sinks = _SQL_SINKS if all_sinks else _SQL_SINKS[:1]
    for node, call in function.calls():
        if call.receiver is None or not call.args:
            continue
        receiver = _unqualified(
            function.receiver_type(call.receiver) if all_sinks
            else function.parameter_types.get(kt.text(call.receiver), "")
        )
        if not any(receiver in types and call.name in methods for types, methods in sinks):
            continue
        sql = function.source.resolve(call.args[0])
        content = kt.string_content(sql)
        if content is None and sql.type == "binary_expression" and kt.operator(sql) == "+":
            pieces = [kt.string_content(item) or "" for item in kt.walk(sql, "string_literal")]
            content = next(iter(pieces), "")
        if content and _SQL_START.match(content):
            yield node, call, sql, content


def _sql_finding(function: Function, node: Node, sql: Node) -> Finding:
    # A query built in a local variable is reported from its construction to the executing call.
    inside_call = node.start_byte <= sql.start_byte < node.end_byte
    in_function = function.body.start_byte <= sql.start_byte < function.body.end_byte
    if in_function and not inside_call and sql.start_byte < node.start_byte:
        return _finding("sql-injection", function, sql, node)
    return _finding("sql-injection", function, node)


def _resolved_calls(
    caller: Function, index: dict[tuple[str, str], list[Function]], implementations: dict[str, list[str]]
) -> Iterator[tuple[kt.Call, Function]]:
    """Calls with a statically known target, following interfaces to their implementing classes."""
    for _, call in caller.calls():
        owner = caller.owner if call.receiver is None else caller.receiver_type(call.receiver)
        if not owner:
            continue
        for kind in (owner, *implementations.get(owner, ())):
            for function in index.get((kind, call.name), []):
                if len(function.parameters) == len(call.args):
                    yield call, function


def _sql_injection_findings(
    functions: list[Function], index: dict[tuple[str, str], list[Function]], implementations: dict[str, list[str]],
) -> list[Finding]:
    """Follow request values from handlers through up to three calls into query APIs."""
    found: dict[tuple[str, int], Finding] = {}
    seen: dict[tuple[str, int, frozenset[str]], int] = {}

    def visit(function: Function, tainted: frozenset[str], depth: int) -> None:
        tainted = tainted - function.scalar_parameters
        key = (function.path, function.node.id, tainted)
        if not tainted or seen.get(key, depth + 1) <= depth:
            return
        seen[key] = depth
        for node, _, sql, _ in _sql_calls(function, all_sinks=True):
            if _uses_names(function.source, sql, tainted):
                finding = _sql_finding(function, node, sql)
                found.setdefault((finding.path, finding.line), finding)
        if depth >= 3:
            return
        for call, target in _resolved_calls(function, index, implementations):
            passed = frozenset(
                name for arg, name in zip(call.args, target.parameters)
                if _uses_names(function.source, arg, tainted)
            )
            visit(target, passed, depth + 1)

    for function in functions:
        visit(function, function.inputs | function.request_parameters, 0)
    return list(found.values())


def _uses_names(source: kt.KotlinFile, node: Node, names: frozenset[str], depth: int = 0) -> bool:
    if depth >= 12:
        return False
    node = source.resolve(node)
    if node.type == "identifier":
        return kt.text(node) in names
    if (value := kt.string_content(node)) is not None:
        for match in kt.TEMPLATE.finditer(value):
            expression = match["name"] or match["braced"]
            if not expression:
                continue
            name = re.match(r"\s*([A-Za-z_]\w*)", expression)
            if name:
                binding = source.binding(name[1], node)
                if binding is not None and _uses_names(source, binding, names, depth + 1):
                    return True
                if binding is None and name[1] in names:
                    return True
        return False
    if node.type in {"call_expression", "navigation_expression", "binary_expression", "value_arguments", "value_argument"}:
        return any(_uses_names(source, child, names, depth + 1) for child in kt.children(node))
    return False


def _tls_findings(function: Function) -> Iterator[Finding]:
    for assignment in kt.walk(function.body, "assignment", stop=_FUNCTION_SCOPE):
        left, right = (assignment.child_by_field_name(side) for side in ("left", "right"))
        if left is None or right is None or _short(left) != "hostnameVerifier":
            continue
        builder = kt.call(function.source.resolve(right))
        if builder is None or builder.name != "HostnameVerifier" or builder.block is None:
            continue
        statements = [node for node in kt.children(builder.block) if node.type != "lambda_parameters"]
        if len(statements) != 1 or kt.text(statements[0]) not in {"true", "return true"}:
            continue
        scope = assignment.parent
        while scope and scope.type not in {"lambda_literal", "function_body"}:
            scope = scope.parent
        apply = scope.parent.parent if scope and scope.type == "lambda_literal" and scope.parent else None
        context = kt.call(apply) if apply is not None else None
        receiver_is_https = (
            context is not None and context.name in {"apply", "also"} and context.receiver is not None
            and "HttpsURLConnection" in kt.text(function.source.resolve(context.receiver))
        )
        typed_connection = left.type == "navigation_expression" and (
            _unqualified(function.parameter_types.get(kt.text(kt.children(left)[0]), "")) == "HttpsURLConnection"
        )
        if receiver_is_https or typed_connection:
            yield _finding("tls-verification", function, assignment)
    for node, call in function.calls():
        if call.name in {"trustManager", "setHostnameVerifier", "hostnameVerifier"} and call.args:
            arg = kt.text(function.source.resolve(call.args[0]))
            if re.fullmatch(r"(?:[\w.]*\.)?(?:InsecureTrustManagerFactory|NoopHostnameVerifier)\.INSTANCE", arg):
                yield _finding("tls-verification", function, node)


@dataclass(frozen=True)
class AccessRule:
    function: Function
    node: Node
    action: str
    method: str
    pattern: str

    def matches(self, route: Route) -> bool:
        if self.method != "*" and self.method != route.method:
            return False
        parts = self.pattern.strip("/").split("/")
        if parts == ["**"]:
            return True
        pattern = ""
        for part in parts:
            if part == "**":
                pattern += r"(?:/.*)?"
            elif part == "*" or (part.startswith("{") and part.endswith("}")):
                pattern += "/[^/]+"
            else:
                pattern += "/" + re.escape(part).replace(r"\*", "[^/]*")
        return re.fullmatch(pattern, route.path) is not None


def _actuator_access(properties: dict[str, str], rules: list[AccessRule]) -> Iterator[Finding]:
    if not rules:
        return
    include = set(map(str.strip, properties.get("management.endpoints.web.exposure.include", "").split(",")))
    exclude = set(map(str.strip, properties.get("management.endpoints.web.exposure.exclude", "").split(",")))
    base_path = properties.get("management.endpoints.web.base-path", "/actuator").rstrip("/")
    if not base_path.startswith("/") or "*" in exclude or properties.get("management.server.port"):
        return
    for endpoint, method in _ACTUATOR_METHODS.items():
        if endpoint in exclude or ("*" not in include and endpoint not in include):
            continue
        if (
            properties.get(f"management.endpoint.{endpoint}.enabled") == "false"
            or properties.get(f"management.endpoint.{endpoint}.access") == "none"
        ):
            continue
        route = Route(method, f"{base_path}/{endpoint}", rules[0].function)
        access = next((rule for rule in rules if rule.matches(route)), None)
        if access and access.action == "permitAll":
            yield _finding("actuator-exposure", access.function, access.node)


def _config_findings(scan: Scan, rules: list[AccessRule], *, check_error_responses: bool) -> Iterator[Finding]:
    if not rules and not check_error_responses:
        return
    for path, content in scan.files.items():
        if path.endswith(".kt"):
            continue
        try:
            for settings in spring_settings(path, content):
                properties = {key: value for key, (value, _) in settings.items()}
                yield from _actuator_access(properties, rules)
                if check_error_responses and properties.get("server.error.include-stacktrace", "").lower() == "always":
                    line = settings["server.error.include-stacktrace"][1]
                    yield _located_finding("error-stacktrace", path, line, line)
        except (yaml.YAMLError, RecursionError):
            scan.skip(path, "SPRING_CONFIG_PARSE_ERROR", "Не удалось проверить конфигурацию Spring Boot.")


def _security(functions: list[Function], scan: Scan) -> tuple[list[AccessRule], bool]:
    chains = [
        function for function in functions
        if any(_unqualified(kind) == "HttpSecurity" for kind in function.parameter_types.values())
    ]
    if len(chains) != 1:
        if chains:
            scan.warn(Warning("UNRESOLVED_SECURITY_CHAIN", "Несколько цепочек Spring Security требуют ручной проверки."), incomplete=True)
        return [], False
    chain = chains[0]
    rules: list[AccessRule] = []
    cors = False
    authorization = []
    for _, call in chain.calls():
        if call.name == "cors" and call.receiver is not None:
            cors = call.block is None or not any(
                (nested := kt.call(node)) and nested.name == "disable"
                for node in kt.walk(call.block, "call_expression")
            )
        elif call.name == "authorizeHttpRequests" and call.block is not None:
            authorization.append(call.block)
    if len(authorization) != 1:
        scan.warn(Warning("UNRESOLVED_SECURITY_CHAIN", "Не удалось определить порядок правил Spring Security.", chain.path), incomplete=True)
        return [], cors
    for node in kt.walk(authorization[0], "call_expression", stop=_FUNCTION_SCOPE):
        call = kt.call(node)
        if call is None:
            continue
        if call.name not in {"permitAll", "authenticated", "hasRole", "hasAnyRole", "hasAuthority", "denyAll"} or call.receiver is None:
            continue
        matcher = kt.call(call.receiver)
        if matcher and matcher.name == "anyRequest":
            rules.append(AccessRule(chain, node, call.name, "*", "/**"))
        if matcher and matcher.name in {"requestMatchers", "antMatchers"} and matcher.args:
            first = _short(matcher.args[0])
            has_method = first in _MAPPING_METHODS.values()
            options = matcher.args[1:] if has_method else matcher.args
            method = first if has_method else "*"
            paths = [kt.literal(arg) for arg in options]
            if paths and all(value and value.startswith("/") for value in paths):
                for value in paths:
                    rules.append(AccessRule(chain, node, call.name, method, value))
                continue
        elif matcher and matcher.name == "anyRequest":
            continue
        scan.warn(Warning("UNRESOLVED_SECURITY_RULE", "Поддерживается сопоставление статических шаблонов маршрутов.", chain.path), incomplete=True)
        return rules, cors
    return rules, cors


def _direct_calls(
    caller: Function, index: dict[tuple[str, str], list[Function]]
) -> Iterator[tuple[kt.Call, Function]]:
    for _, call in caller.calls():
        owner = caller.owner if call.receiver is None else caller.parameter_types.get(kt.text(call.receiver))
        if owner is None:
            continue
        candidates = [
            function for function in index.get((owner, call.name), [])
            if len(function.parameters) == len(call.args)
        ]
        if len(candidates) == 1:
            yield call, candidates[0]


def _sensitive(function: Function) -> bool:
    if function.principal:
        return True
    return any(_SENSITIVE.search(sql) for _, _, _, sql in _sql_calls(function)) or any(
        _SENSITIVE.search(value) for _, value in _literal_nodes(function.body)
    )


def _checked(function: Function) -> bool:
    return function.guarded or any(
        call.name in {"hasRole", "hasAuthority", "isUserInRole", "checkPermission", "requireRole"}
        for _, call in function.calls()
    )


def _cors_findings(
    functions: list[Function], routes: list[Route], rules: list[AccessRule], scan: Scan
) -> Iterator[Finding]:
    def unresolved(function: Function) -> None:
        scan.warn(Warning("UNRESOLVED_CORS", "Не удалось статически разобрать регистрацию CORS.", function.path), incomplete=True)

    for function in functions:
        for _, call in function.calls():
            if call.name != "registerCorsConfiguration" or len(call.args) != 2:
                continue
            path = kt.literal(call.args[0])
            if not path:
                unresolved(function)
                continue
            config = function.source.resolve(call.args[1])
            builder = kt.call(config)
            if not builder or builder.name != "apply" or builder.block is None or builder.receiver is None:
                unresolved(function)
                continue
            constructor = kt.call(builder.receiver)
            if constructor is None or constructor.name != "CorsConfiguration":
                unresolved(function)
                continue
            fields = {}
            for entry in kt.children(builder.block):
                if entry.type == "assignment":
                    left, right = (entry.child_by_field_name(side) for side in ("left", "right"))
                    if left is not None and right is not None:
                        fields[_short(left)] = (entry, function.source.resolve(right))
            origins, credentials = fields.get("allowedOriginPatterns"), fields.get("allowCredentials")
            if not origins or not credentials:
                continue
            credential_value = kt.text(credentials[1])
            origin_values = {origin for _, origin in _literal_nodes(origins[1])}
            if credential_value not in {"true", "false"} or not origin_values:
                unresolved(function)
                continue
            if credential_value != "true" or not origin_values & {"*", "http://*", "https://*"}:
                continue
            methods = {value.upper() for _, value in _literal_nodes(fields["allowedMethods"][1])} if "allowedMethods" in fields else set()
            matcher = AccessRule(function, origins[0], "", "*", path)
            if any(
                matcher.matches(route) and (not methods or "*" in methods or route.method in methods) and (
                    route.function.principal
                    or (
                        _sensitive(route.function)
                        and (access := next((rule for rule in rules if rule.matches(route)), None))
                        and access.action in {"authenticated", "hasRole", "hasAnyRole", "hasAuthority"}
                    )
                )
                for route in routes
            ):
                first, last = sorted((origins[0], credentials[0]), key=lambda node: node.start_byte)
                yield _finding("credentialed-cors", function, first, last)


def _checks_owner_before_return(function: Function, principal: frozenset[str]) -> bool:
    block = kt.child(function.body, "block")
    if block is None or not principal:
        return False
    for condition in kt.children(block):
        if condition.type != "if_expression":
            continue
        test = condition.child_by_field_name("condition")
        parts = kt.children(condition)
        if test is None or len(parts) < 2 or kt.operator(test) != "!=":
            continue
        branches = kt.children(parts[1]) if parts[1].type == "block" else [parts[1]]
        if (
            branches and branches[-1].type in {"return_expression", "throw_expression"}
            and _OWNERS.search(kt.text(test)) and _uses_names(function.source, test, principal)
        ):
            if branches[-1].type == "throw_expression" or kt.text(branches[-1]).strip() in {"return", "return null"}:
                return True
    return False


def _jdbc_values(args: tuple[Node, ...]) -> list[Node]:
    return [
        arg for arg in args[1:]
        if arg.type not in {"lambda_literal", "annotated_lambda"}
        and not re.search(r"::class\.java(?:ObjectType)?$", kt.text(arg))
    ]


def _object_findings(
    route: Route, connections: list[tuple[kt.Call, Function]], scan: Scan
) -> Iterator[Finding]:
    handler = route.function
    candidates = [(handler, handler.inputs, handler.principal)]
    for call, target in connections:
        parameters = target.parameters
        inputs = frozenset(
            name for arg, name in zip(call.args, parameters)
            if _uses_names(handler.source, arg, handler.inputs)
        )
        principal = frozenset(
            name for arg, name in zip(call.args, parameters)
            if _uses_names(handler.source, arg, handler.principal)
        )
        if inputs:
            candidates.append((target, inputs, principal))
    for function, inputs, principal in candidates:
        for node, call, _, sql in _sql_calls(function):
            where = re.search(r"\bwhere\b", sql, re.IGNORECASE)
            if where is None or not _OWNERS.search(sql[:where.start()]):
                continue
            values = _jdbc_values(call.args)
            if not any(_uses_names(function.source, arg, inputs) for arg in values):
                continue
            if _checks_owner_before_return(function, principal) or _checks_owner_before_return(handler, handler.principal):
                continue
            predicate = sql[where.end():]
            owner_filter = _OWNER_BIND.search(predicate)
            if owner_filter and not re.search(r"\bor\b", predicate, re.IGNORECASE):
                position = sql.count("?", 0, where.end() + owner_filter.start(1))
                if position < len(values):
                    bound = values[position]
                    if _uses_names(function.source, bound, principal):
                        continue
                    if _uses_names(function.source, bound, inputs):
                        yield _finding("object-authorization", function, node)
                        continue
            if _OWNERS.search(predicate):
                scan.warn(Warning("UNRESOLVED_OWNER_CHECK", "Не удалось подтвердить источник идентификатора владельца.", function.path), incomplete=True)
                continue
            yield _finding("object-authorization", function, node)


def _spring_data(kind: str, supertypes: dict[str, list[str]], depth: int = 0) -> bool:
    """Spring Data generates parameterized queries for repository interfaces without custom code."""
    return depth < 5 and any(
        _unqualified(parent) in _SPRING_DATA_REPOSITORIES or _spring_data(parent, supertypes, depth + 1)
        for parent in supertypes.get(kind, [])
    )


def _index_snapshot(scan: Scan) -> tuple[list[Function], list[Route], dict[str, list[str]]]:
    functions: list[Function] = []
    routes: list[Route] = []
    supertypes: dict[str, list[str]] = {}
    for path, data in scan.files.items():
        if not path.endswith(".kt"):
            continue
        source = kt.KotlinFile(data)
        if source.root.has_error:
            scan.skip(path, "KOTLIN_PARSE_ERROR", "Файл Kotlin разобран частично.")
        current_functions, current_routes, current_supertypes = _index(path, source)
        functions.extend(current_functions)
        routes.extend(current_routes)
        supertypes.update(current_supertypes)
    return functions, routes, supertypes


def _spring_security_findings(
    scan: Scan, functions: list[Function], routes: list[Route], *, check_error_responses: bool,
) -> tuple[list[AccessRule], list[Finding]]:
    rules, cors = _security(functions, scan)
    findings = list(_config_findings(scan, rules, check_error_responses=check_error_responses))
    if cors:
        findings.extend(_cors_findings(functions, routes, rules, scan))
    return rules, findings


def scan_spring_config(snapshot: Snapshot) -> ScanResult:
    scan = Scan(snapshot, accepts_spring_source)
    functions, routes, _ = _index_snapshot(scan)
    _, findings = _spring_security_findings(scan, functions, routes, check_error_responses=True)
    return scan.result("sast", "Настройки Spring Boot, Actuator и CORS", findings)


def scan_sast(snapshot: Snapshot) -> ScanResult:
    scan = Scan(snapshot, accepts_sast_source)
    if scan.files and not any(path.endswith(".kt") for path in scan.files):
        scan.warn(Warning("NO_KOTLIN_SOURCES", "В снимке нет доступных файлов Kotlin."), incomplete=True)
    functions, routes, supertypes = _index_snapshot(scan)
    rules, findings = _spring_security_findings(scan, functions, routes, check_error_responses=False)
    index: dict[tuple[str, str], list[Function]] = {}
    for function in functions:
        index.setdefault((function.owner, function.name), []).append(function)
        findings.extend(_tls_findings(function))
    implementations: dict[str, list[str]] = {}
    for kind, parents in supertypes.items():
        for parent in parents:
            implementations.setdefault(parent, []).append(kind)
    findings.extend(_sql_injection_findings(functions, index, implementations))

    for route in routes:
        handler = route.function
        connections = list(_direct_calls(handler, index))
        rule = next((rule for rule in rules if rule.matches(route)), None)
        if rule and rule.action == "permitAll" and not _checked(handler):
            targets = [target for _, target in connections]
            if _sensitive(handler) or any(_sensitive(target) and not _checked(target) for target in targets):
                findings.append(_finding("route-authorization", rule.function, rule.node))
        if handler.inputs:
            findings.extend(_object_findings(route, connections, scan))
            resolved = {call.name for call, _ in _resolved_calls(handler, index, implementations)}
            if not connections and any(
                call.receiver is not None and kt.text(call.receiver) in handler.parameter_types
                and _unqualified(handler.parameter_types[kt.text(call.receiver)]) not in _JDBC_TYPES | _FRAMEWORK_TYPES
                and call.name not in resolved
                and not _spring_data(handler.parameter_types[kt.text(call.receiver)], supertypes)
                and any(_uses_names(handler.source, arg, handler.inputs) for arg in call.args)
                for _, call in handler.calls()
            ):
                scan.warn(Warning("UNRESOLVED_CALL", "Не удалось связать обработчик с вызываемым методом.", handler.path), incomplete=True)
    return scan.result("sast", "Правила Kotlin, JDBC, JPA и Spring Security", findings)
