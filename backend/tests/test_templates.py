from secrets import randbits
from uuid import UUID

from fastapi.testclient import TestClient

from backend.app.db.models import WorkflowTemplate

from backend.tests.test_api import ORIGIN, analysis_graph, api, create_project, create_workflow, edge, login, node


def test_author_publishes_private_draft_to_the_team_library(api, monkeypatch):
    author, _ = api
    team = author.post("/api/workspaces", json={"name": "Аналитики"}).json()["id"]
    project = create_project(author)
    assert author.post(f"/api/projects/{project}/move", json={"workspace_id": team}).status_code == 200
    graph = analysis_graph("ci_trigger")
    graph["nodes"][0]["config"] = {"branch": "internal"}
    graph["nodes"].append(node("email", "notify_email", {
        "recipients": ["me@example.org"], "subject": "Private", "attach_json": False,
        "credential_id": "00000000-0000-0000-0000-000000000123",
    }))
    graph["edges"].append(edge("deliver", "merge", "bundle", "email", "report"))
    workflow = create_workflow(author, project, graph)
    draft = author.post("/api/templates", json={
        "workflow_id": workflow, "name": "Проверки CI", "description": "Проверки перед слиянием",
    }).raise_for_status().json()
    path = "/api/templates/" + draft["id"]
    catalog = "/api/templates?workspace_id=" + team
    publish = path + "/publish"

    with TestClient(author.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as colleague:
        login(colleague, monkeypatch, randbits(48) + 1)
        personal_project = create_project(colleague)
        assert colleague.get(catalog).status_code == 404
        assert colleague.post(path + "/copy", json={"project_id": personal_project}).status_code == 404
        invitation = author.post(f"/api/workspaces/{team}/invitations", json={"role": "member"}).json()
        colleague.post(f"/api/workspaces/{team}/join", json={"token": invitation["token"]}).raise_for_status()
        assert colleague.get(catalog).json()["items"] == []
        assert colleague.get(path).status_code == 404
        assert colleague.post(publish, json={"workspace_id": team}).status_code in (403, 404)
        other_team = author.post("/api/workspaces", json={"name": "Другая команда"}).json()["id"]
        assert author.post(publish, json={"workspace_id": other_team}).status_code == 409

        published = author.post(publish, json={"workspace_id": team}).raise_for_status().json()
        assert published["id"] == draft["id"] and published["workspace_id"] == team
        assert published["description"] == "Проверки перед слиянием"
        assert [item["id"] for item in colleague.get(catalog).json()["items"]] == [draft["id"]]
        nodes = published["graph"]["nodes"]
        assert nodes[0]["config"] == {} and nodes[-1]["config"] == {"attach_json": False}
        assert "project_id" not in published
        assert colleague.post(path + "/copy", json={"project_id": project}).status_code == 403
        copy = colleague.post(path + "/copy", json={"project_id": personal_project}).raise_for_status().json()
        assert copy["project_id"] == personal_project
        copy_path = "/api/workflows/" + copy["id"]
        colleague.patch(copy_path, json={"name": "Моя проверка", "graph": analysis_graph()}).raise_for_status()
        assert colleague.get(copy_path).json()["graph"] == analysis_graph()
        assert author.get(path).json()["graph"] == published["graph"]
        assert author.get(f"/api/workflows/{workflow}").json()["graph"] == graph
        member = colleague.get("/api/me").json()["id"]
        author.delete(f"/api/workspaces/{team}/members/{member}").raise_for_status()
        assert colleague.get(path).status_code == 404
        assert colleague.post(path + "/copy", json={"project_id": personal_project}).status_code == 404
        assert colleague.get(copy_path).status_code == 200


def test_published_schedule_copies_into_personal_project_but_not_another_team(api):
    client, _ = api
    team = client.post("/api/workspaces", json={"name": "Публикации"}).json()["id"]
    other_team = client.post("/api/workspaces", json={"name": "Другой проект"}).json()["id"]
    personal_project = create_project(client)
    source = create_workflow(client, personal_project, analysis_graph("schedule_trigger"))
    client.put(f"/api/workflows/{source}/schedule", json={
        "enabled": True, "time": "08:30", "timezone": "UTC",
    }).raise_for_status()
    draft = client.post("/api/templates", json={"workflow_id": source, "name": "Утренний запуск"}).json()
    path = "/api/templates/" + draft["id"]
    client.post(path + "/publish", json={"workspace_id": team}).raise_for_status()
    other_project = client.post("/api/projects", json={
        "name": "Соседний проект", "repository_url": "https://github.com/TBagirov/training", "workspace_id": other_team,
    }).raise_for_status().json()["id"]
    assert client.post(path + "/copy", json={"project_id": other_project}).status_code == 409
    copy = client.post(path + "/copy", json={"project_id": personal_project}).raise_for_status().json()
    schedule = client.get(f"/api/workflows/{copy['id']}/schedule").json()
    assert schedule["enabled"] is False and schedule["next_run_at"] is None and schedule["time"] == "08:30"
    assert client.get(f"/api/workflows/{source}/schedule").json()["enabled"] is True


def test_legacy_draft_requires_source_confirmation_before_publication(api):
    client, sessions = api
    user_id = UUID(client.get("/api/me").json()["id"])
    team = client.post("/api/workspaces", json={"name": "Назначение"}).json()["id"]
    with sessions.begin() as db:
        legacy = WorkflowTemplate(owner_id=user_id, name="Старый черновик", description="", graph_json=analysis_graph())
        db.add(legacy)
        db.flush()
        path = f"/api/templates/{legacy.id}"
    assert client.get(path).status_code == 200
    assert client.post(path + "/publish", json={"workspace_id": team}).status_code == 409
    source = create_workflow(client, create_project(client), analysis_graph())
    client.patch(path, json={"workflow_id": source}).raise_for_status()
    assert client.post(path + "/publish", json={"workspace_id": team}).status_code == 200
