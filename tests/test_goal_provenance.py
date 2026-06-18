# tests/test_goal_provenance.py
"""v1.6 (#7): goal provenance gate (diagnostic-only).

A goal's origin decides whether the actor may auto-execute it. Externally or
environmentally grounded origins (user request, perceived anomaly, observed
maintenance need) are executable; self-referential / error / compaction-derived
origins are diagnostic-only — visible to the host but never auto-run. This is
the structural generalization of the v1.5.1 #6 slice (which removed exactly one
diagnostic origin, recurring errors, from minting executable goals).

Provenance already rides the free-form Goal.source string, so the taxonomy maps
onto existing values with zero storage migration.
"""
from conscio.goal_generator import Drive, Goal, GoalGenerator, GoalOrigin


class TestGoalOriginClassification:
    def test_executable_origins(self):
        for o in (GoalOrigin.USER, GoalOrigin.INTERNAL, GoalOrigin.CURIOSITY,
                  GoalOrigin.ANOMALY, GoalOrigin.MAINTENANCE):
            assert o.auto_executable is True, o

    def test_diagnostic_origins(self):
        for o in (GoalOrigin.META_ERROR, GoalOrigin.SELF_PROMPT,
                  GoalOrigin.COMPACTION):
            assert o.auto_executable is False, o


class TestGoalExecutable:
    def _goal(self, source):
        return Goal(description="x", drive=Drive.CURIOSITY, source=source)

    def test_user_goal_is_executable(self):
        assert self._goal("user").executable is True

    def test_internal_goal_is_executable(self):
        assert self._goal("internal").executable is True

    def test_self_prompt_goal_is_diagnostic(self):
        assert self._goal("self_prompt").executable is False

    def test_meta_error_goal_is_diagnostic(self):
        assert self._goal("meta_error").executable is False

    def test_compaction_goal_is_diagnostic(self):
        assert self._goal("compaction").executable is False

    def test_legacy_unknown_source_defaults_executable(self):
        # Back-compat: a free-form source from an older goals.json must not be
        # silently denied — unknown -> INTERNAL (executable).
        g = self._goal("some_old_freeform_value")
        assert g.origin is GoalOrigin.INTERNAL
        assert g.executable is True

    def test_origin_property_roundtrips_known_source(self):
        assert self._goal("self_prompt").origin is GoalOrigin.SELF_PROMPT


class TestGeneratorIsExecutable:
    def test_diagnostic_active_goal_is_not_executable(self, tmp_path):
        gen = GoalGenerator(tmp_path)
        g = gen.generate_from_curiosity("the moon", source="self_prompt")
        assert gen.is_executable(g.description) is False

    def test_executable_active_goal_is_executable(self, tmp_path):
        gen = GoalGenerator(tmp_path)
        g = gen.generate_from_curiosity("the comet", source="internal")
        assert gen.is_executable(g.description) is True

    def test_unknown_description_defaults_executable(self, tmp_path):
        # A raw-string goal not tracked by the generator (e.g. bench/tests)
        # must pass the gate — the gate only denies a *known diagnostic* goal.
        gen = GoalGenerator(tmp_path)
        assert gen.is_executable("a goal the generator never made") is True


class TestUserGoalOrigin:
    def test_add_user_goal_defaults_user_origin(self, tmp_path):
        gen = GoalGenerator(tmp_path)
        g = gen.add_user_goal("do the thing")
        assert g.origin is GoalOrigin.USER
        assert g.executable is True

    def test_host_can_tag_compaction_origin_diagnostic(self, tmp_path):
        # #7 field failure: compaction-fabricated tasks auto-executed. A host
        # that knows content is compaction-derived routes it diagnostically.
        gen = GoalGenerator(tmp_path)
        g = gen.add_user_goal("instruction from compacted context",
                              origin=GoalOrigin.COMPACTION)
        assert g.executable is False
        assert gen.is_executable(g.description) is False
