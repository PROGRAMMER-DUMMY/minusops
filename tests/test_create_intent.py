"""
P4.3: `minusctl create` must not silently do nothing.

is_creation_request() required a create VERB and an infra NOUN. So:
    create "create a governed AWS data pipeline"   -> run workspace
    create "governed AWS data pipeline"            -> classified OPERATION, NOTHING created

Hit live while walking through the tool. Any agent phrasing the request naturally -- and
the user already typed `create`, so repeating the verb is redundant -- gets a success-looking
message and no run. Silent no-op is the worst failure shape for an agent-driven CLI.
"""
import intent_resolver


def test_explicit_create_verb_still_recognised():
    assert intent_resolver.is_creation_request("create a governed AWS data pipeline")
    assert intent_resolver.is_creation_request("build a lakehouse stack")


def test_bare_infra_noun_phrase_is_a_creation_request():
    """The regression. The user invoked `create`; the noun phrase is the subject."""
    for phrase in ("governed AWS data pipeline for clickstream analytics",
                   "a medallion lakehouse",
                   "streaming ingestion pipeline"):
        assert intent_resolver.is_creation_request(phrase), phrase


def test_operational_questions_are_still_not_creation_requests():
    """Must not over-trigger: asking ABOUT a pipeline is not asking to build one."""
    for phrase in ("what does my pipeline cost",
                   "show me the pipeline health",
                   "why did the pipeline fail last night"):
        assert not intent_resolver.is_creation_request(phrase), phrase


def test_empty_and_junk_are_not_creation_requests():
    for phrase in ("", "   ", "hello"):
        assert not intent_resolver.is_creation_request(phrase)


def test_operations_on_existing_infrastructure_are_not_creation_requests():
    """Caught by the existing dispatcher suite when the noun-phrase rule was first loosened:
    'deploy this infrastructure' is a DEPLOY operation, not a design request."""
    for phrase in ("please deploy this infrastructure",
                   "destroy the lakehouse stack",
                   "optimize my glue pipeline",
                   "scan the terraform for issues"):
        assert not intent_resolver.is_creation_request(phrase), phrase


def test_an_explicit_create_verb_beats_an_operational_word():
    """'create the pipeline then deploy it' is still a creation request."""
    assert intent_resolver.is_creation_request("create the pipeline then deploy it")
