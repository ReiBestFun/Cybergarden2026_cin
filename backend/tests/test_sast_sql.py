"""SQL injection through JPA/JDBC APIs, Spring request binding and calls across classes."""

from backend.app.analyzers import Snapshot, scan_sast

SHA = "a" * 40

CONTROLLER = """
    package demo.web
    import demo.store.OwnerRepository
    @Controller
    class OwnerController(val owners: OwnerRepository) {
        @GetMapping("/owners")
        fun find(owner: Owner, result: BindingResult, model: MutableMap<String, Any>): String {
            model["owners"] = owners.findByLastName(owner.lastName)
            return "owners/list"
        }
        @GetMapping("/owners/{ownerId}")
        fun show(@PathVariable ownerId: Int, model: Model): String {
            model.addAttribute(owners.findById(ownerId))
            return "owners/details"
        }
    }
"""
REPOSITORY = """
    package demo.store
    interface OwnerRepository {
        fun findByLastName(lastName: String): Collection<Owner>
        fun findById(id: Int): Owner
    }
"""


def sql_lines(files: dict[str, str]) -> list[tuple[str, int]]:
    result = scan_sast(Snapshot(SHA, files))
    return sorted((item.path, item.line) for item in result.findings if item.metadata["class"] == "sql-injection")


def test_jpql_injection_through_repository_implementation():
    implementation = """
        package demo.store
        @Component
        class OwnerRepositoryImpl : OwnerRepository {
            @PersistenceContext
            private val entityManager: EntityManager? = null
            override fun findByLastName(lastName: String): Collection<Owner> {
                val jpql =
                    "SELECT DISTINCT o FROM Owner o WHERE o.lastName = '$lastName'"
                return entityManager!!.createQuery(jpql, Owner::class.java).resultList
            }
            override fun findById(id: Int): Owner {
                val jpql = "SELECT o FROM Owner o WHERE o.id = $id"
                return entityManager!!.createQuery(jpql, Owner::class.java).singleResult
            }
        }
    """
    result = scan_sast(Snapshot(SHA, {
        "src/web/OwnerController.kt": CONTROLLER, "src/store/OwnerRepository.kt": REPOSITORY,
        "src/store/OwnerRepositoryImpl.kt": implementation,
    }))
    findings = [item for item in result.findings if item.metadata["class"] == "sql-injection"]
    # Only the String parameter is injectable; the Int id in findById is not reported.
    assert [(item.path, item.line, item.end_line) for item in findings] == [("src/store/OwnerRepositoryImpl.kt", 9, 10)]
    assert result.coverage.status == "complete"


def test_parameterized_jpql_and_unreachable_code_are_not_reported():
    implementation = """
        package demo.store
        class OwnerRepositoryImpl(private val em: EntityManager) : OwnerRepository {
            override fun findByLastName(lastName: String): Collection<Owner> =
                em.createQuery("SELECT o FROM Owner o WHERE o.lastName = :name", Owner::class.java)
                    .setParameter("name", lastName).resultList
            override fun findById(id: Int): Owner = em.find(Owner::class.java, id)
        }
    """
    helper = """
        package demo.tools
        class Maintenance(private val em: EntityManager) {
            // Not reachable from a request handler: the argument is not user input.
            fun purge(table: String) = em.createNativeQuery("DELETE FROM $table").executeUpdate()
        }
    """
    assert sql_lines({
        "src/web/OwnerController.kt": CONTROLLER, "src/store/OwnerRepository.kt": REPOSITORY,
        "src/store/OwnerRepositoryImpl.kt": implementation, "src/tools/Maintenance.kt": helper,
    }) == []


def test_statement_concatenation_two_calls_deep():
    files = {
        "src/web/SearchController.kt": """
            package demo.web
            import demo.service.SearchService
            @RestController
            class SearchController(private val service: SearchService) {
                @GetMapping("/search")
                fun search(@RequestParam("q") query: String) = service.run(query.trim())
            }
        """,
        "src/service/SearchService.kt": """
            package demo.service
            class SearchService(private val dao: SearchDao) {
                fun run(text: String) = dao.lookup(text)
            }
        """,
        "src/service/SearchDao.kt": """
            package demo.service
            class SearchDao(private val dataSource: DataSource) {
                fun lookup(term: String): Int {
                    val connection = dataSource.getConnection()
                    val statement = connection.createStatement()
                    return statement.executeUpdate("UPDATE hits SET n = n + 1 WHERE term = '" + term + "'")
                }
            }
        """,
    }
    assert sql_lines(files) == [("src/service/SearchDao.kt", 7)]
    files["src/service/SearchDao.kt"] = """
        package demo.service
        class SearchDao(private val statement: Statement) {
            fun lookup(term: String): Int =
                statement.executeUpdate("UPDATE hits SET n = n + 1 WHERE term = ?")
        }
    """
    assert sql_lines(files) == []


def test_spring_data_repository_calls_are_resolved():
    repository = """
        package demo.store
        interface OwnerRepository : JpaRepository<Owner, Int> {
            fun findByLastName(lastName: String): Collection<Owner>
            fun findById(id: Int): Owner
        }
    """
    result = scan_sast(Snapshot(SHA, {"src/web/OwnerController.kt": CONTROLLER, "src/store/OwnerRepository.kt": repository}))
    assert result.findings == ()
    assert result.coverage.status == "complete" and not result.warnings
