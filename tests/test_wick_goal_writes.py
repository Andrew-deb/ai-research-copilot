"""Goal mutations share validation and retain actor, ownership and mode boundaries."""
from unittest.mock import Mock
import pytest
from mcp_server.shared_resource.services import goal_service as domain
from mcp_server.shared_resource.exceptions import ValidationError, GoalNotFoundError
from services import goal_service, agent_service
from exceptions import ValidationError as AppValidationError, GoalNotFoundError as AppGoalNotFoundError, CapabilityDeniedError

@pytest.mark.parametrize('title,description', [('',None), ('x'*301,None), (None,None), ('Goal',17)])
def test_invalid_goal_never_reaches_persistence(title, description):
    repo = Mock()
    with pytest.raises(ValidationError):
        domain.create_learning_goal(repo, 'owner', title, description)
    repo.create_learning_goal.assert_not_called()


def test_creation_returns_saved_values_and_normalizes_dashboard_inputs(monkeypatch):
    saved = {'goal_id':'goal', 'user_id':'owner', 'title':'Goal', 'description':'Details', 'status':'active'}
    repo = Mock(); repo.create_learning_goal.return_value = saved
    assert domain.create_learning_goal(repo, 'owner', ' Goal ', ' Details ') == saved
    repo.create_learning_goal.assert_called_once_with(user_id='owner', title='Goal', description='Details')
    monkeypatch.setattr(goal_service, 'lakebase', repo)
    assert goal_service.create_goal('owner', ' Goal ', ' Details ') == saved
    with pytest.raises(AppValidationError):
        goal_service.create_goal('owner', 'x'*301)


@pytest.mark.parametrize('actor,status', [(None,'active'), ('owner','deleted'), ('owner',None)])
def test_invalid_status_or_missing_actor_stops_before_write(actor, status):
    repo = Mock()
    with pytest.raises(ValidationError):
        domain.update_goal_status(repo, 'goal', actor, status)
    repo.update_goal_status.assert_not_called()


def test_status_is_owner_scoped_normalized_and_missing_targets_fail(monkeypatch):
    repo = Mock(); repo.update_goal_status.return_value = {'goal_id':'goal','status':'archived'}
    assert domain.update_goal_status(repo, 'goal', 'owner', ' ARCHIVED ')['status'] == 'archived'
    repo.update_goal_status.assert_called_once_with(goal_id='goal', user_id='owner', status='archived')
    repo.update_goal_status.return_value = None
    with pytest.raises(GoalNotFoundError):
        domain.update_goal_status(repo, 'goal', 'foreign', 'completed')
    monkeypatch.setattr(goal_service, 'lakebase', repo)
    with pytest.raises(AppGoalNotFoundError):
        goal_service.set_status('foreign', 'goal', 'completed')


@pytest.mark.parametrize('tool', ['create_learning_goal','update_goal_status'])
def test_goal_tools_are_authenticated_wick_only_and_obey_write_gate(tool, monkeypatch):
    agent_service.ensure_callable('authenticated', tool, 'wick')
    for tier,mode in [('anonymous','wick'), ('authenticated','research')]:
        with pytest.raises(CapabilityDeniedError):
            agent_service.ensure_callable(tier, tool, mode)
    monkeypatch.setattr(agent_service, 'WRITE_TOOLS_ENABLED', False)
    with pytest.raises(CapabilityDeniedError):
        agent_service.ensure_callable('authenticated', tool, 'wick')


def test_repository_status_update_requires_owner_in_sql(monkeypatch):
    from mcp_server.shared_resource.repositories import lakebase
    query = Mock(return_value=None)
    monkeypatch.setattr(lakebase,'run_write',query)
    assert lakebase.update_goal_status('goal','foreign','completed') is None
    sql,params = query.call_args.args
    assert 'WHERE goal_id = %s AND user_id = %s' in sql
    assert params == ('completed','goal','foreign')
