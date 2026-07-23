from conscio.awake.goal_templates import goals_from_world_state


def test_empty_world_state_no_goals():
    assert goals_from_world_state("") == []
    assert goals_from_world_state("   ") == []


def test_py_modified_generates_goal():
    ws = "[filesystem]\nmodified: /repo/src/app.py"
    goals = goals_from_world_state(ws)
    assert any("verificar" in g and "app.py" in g for g in goals)


def test_test_file_modified_no_goal():
    ws = "[filesystem]\nmodified: /repo/tests/test_app.py"
    goals = goals_from_world_state(ws)
    assert all("test_app" not in g for g in goals)


def test_many_py_grouped():
    ws = (
        "[filesystem]\n"
        "modified: /repo/a.py\n"
        "modified: /repo/b.py\n"
        "modified: /repo/c.py\n"
        "modified: /repo/d.py\n"
        "modified: /repo/e.py"
    )
    goals = goals_from_world_state(ws)
    assert len(goals) == 1
    assert "5 arquivos" in goals[0]


def test_new_commit_generates_goal():
    ws = "[git]\ncommit abc12345 by dev: fix bug"
    goals = goals_from_world_state(ws)
    assert any("abc12345" in g for g in goals)


def test_many_commits_grouped():
    ws = (
        "[git]\n"
        "commit aaaa1111 by x: m1\n"
        "commit bbbb2222 by x: m2\n"
        "commit cccc3333 by x: m3\n"
        "commit dddd4444 by x: m4\n"
        "commit eeee5555 by x: m5\n"
        "commit ffff6666 by x: m6"
    )
    goals = goals_from_world_state(ws)
    assert len(goals) == 1
    assert "6 commits" in goals[0]


def test_md_modified_no_goal():
    ws = "[filesystem]\nmodified: /repo/README.md"
    goals = goals_from_world_state(ws)
    assert goals == []


def test_created_py_generates_goal():
    ws = "[filesystem]\ncreated: /repo/new_module.py"
    goals = goals_from_world_state(ws)
    assert any("new_module.py" in g for g in goals)


def test_deleted_generates_goal():
    ws = "[filesystem]\ndeleted: /repo/old_module.py"
    goals = goals_from_world_state(ws)
    assert any("old_module.py" in g for g in goals)


def test_mixed_signals():
    ws = (
        "[filesystem]\nmodified: /repo/app.py\n\n"
        "[git]\ncommit abc12345 by dev: msg"
    )
    goals = goals_from_world_state(ws)
    assert len(goals) == 2


def test_deterministic():
    ws = "[filesystem]\nmodified: /repo/app.py"
    g1 = goals_from_world_state(ws)
    g2 = goals_from_world_state(ws)
    assert g1 == g2
