"""Exact-context blind-steal learning and conservative policy gates."""
import pickle

import pytest

from card_utils import Card
from game_state import GameState, GameStateView
from model import Model
from opponent_model import OpponentModel
from player_action import PlayerAction


def learn(model, folds, calls=0, size=46, seats=2, threebet=False):
    for i in range(folds + calls):
        state = GameState([500] * seats)
        model.new_hand()
        actions = [PlayerAction.raise_to(0, size)]
        if threebet:
            actions += [PlayerAction.raise_to(1, 140), PlayerAction.raise_to(0, 300)]
        actions.append(PlayerAction.fold(1) if i < folds else PlayerAction.call(1))
        model.end_hand(actions, GameStateView(state), 0)


@pytest.mark.parametrize('folds,calls,supported', [(19,0,False),(20,0,True),(18,2,False),(19,1,True),(30,20,False)])
def test_confidence_gate(folds,calls,supported):
    model=OpponentModel();learn(model,folds,calls)
    assert model.supports_blind_steal('seat 1') is supported


@pytest.mark.parametrize('size,seats,threebet', [(500,2,False),(46,3,False),(46,2,True)])
def test_other_bet_contexts_do_not_support_steals(size,seats,threebet):
    model=OpponentModel();learn(model,30,size=size,seats=seats,threebet=threebet)
    assert model.profile('seat 1')['hu_bb_open_opportunities']==(30 if threebet else 0)
    assert model.profile('seat 1')['hu_bb_open_folds']==0
    assert not model.supports_blind_steal('seat 1')


def test_same_junk_hand_opens_only_after_reliable_big_blind_folds():
    bot=Model();bot.seed([Card.parse('7H'),Card.parse('2C')],0)
    state=GameState([500,500]);view=GameStateView(state)
    assert bot.run([],view).kind=='fold'
    learn(bot.opponent_model,20)
    bot.seed([Card.parse('7H'),Card.parse('2C')],0)
    action=bot.run([],view);state.validate_action(action)
    assert (action.kind,action.amount)==('raise',46)
    bot=pickle.loads(pickle.dumps(bot,protocol=4))
    assert bot.opponent_model.supports_blind_steal('seat 1')
    # The learned team can move to the BB in seat 0.
    state=GameState([500,500]);state.setup_hand([500,500],button=1,teams=['seat 1','hero'])
    bot.seed([Card.parse('7H'),Card.parse('2C')],1)
    action=bot.run([],GameStateView(state));state.validate_action(action)
    assert (action.kind,action.amount)==('raise',46)


def test_short_stacks_keep_original_policy():
    bot=Model();learn(bot.opponent_model,20)
    bot.seed([Card.parse('7H'),Card.parse('2C')],0)
    assert bot.run([],GameStateView(GameState([200,200]))).kind=='fold'


def test_old_pickle_records_without_new_fields_are_safe_and_upgradable():
    model=OpponentModel();learn(model,1)
    record=model._records['seat 1']
    del record['hu_bb_open_opportunities'];del record['hu_bb_open_folds']
    model=pickle.loads(pickle.dumps(model,protocol=4))
    assert not model.supports_blind_steal('seat 1')
    learn(model,20)
    assert model.supports_blind_steal('seat 1')


def test_incremental_observation_and_batch_id_do_not_double_count():
    model=OpponentModel();state=GameState([500,500]);view=GameStateView(state)
    model.new_hand()
    model.observe([PlayerAction.raise_to(0,46)],view,0,batch_id=1)
    model.observe([PlayerAction.fold(1)],view,0,batch_id=2)
    model.observe([PlayerAction.fold(1)],view,0,batch_id=2)
    p=model.profile('seat 1')
    assert p['hu_bb_open_opportunities']==p['hu_bb_open_folds']==1
