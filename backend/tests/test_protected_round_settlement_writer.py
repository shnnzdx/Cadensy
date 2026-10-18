from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread, current_thread
from time import monotonic
from queue import Queue
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker
from app.api import main as api
from app.db import session as db_session
from app.db.models import AuthSession, DecisionRound, GuestSession, MutationIdempotency, Plan, PlanChange, PlanItem, Trip, TripMembership, UpdateNotice, User, Vote
from app.domain import auth as auth
from app.domain.transactions import CredentialProof, IdempotencyKeyReused, PreliminaryActorScope, StalePlanRevision
from app.domain.transactions import round_settlement as protected
from app.domain.transactions import vote as protected_vote
from app.domain.transactions.durable import HmacSigningKey

_trips=[]
@dataclass(frozen=True)
class Seed: trip_id:str; plan_id:str; item_id:str; round_id:str; account_id:str; membership_id:str; token:str; guest_id:str; guest_token:str; guest_session_id:str
def _factory(engine): return sessionmaker(bind=engine, expire_on_commit=False, future=True)
def _opts(): return [{"id":"keep","title":"Round item","patch":{}},{"id":"requested","title":"Changed item","patch":{"title":"Changed item"}},{"id":"remove","title":"Removed","patch":{"remove":True}}]
def _seed(engine, kind="normal", extra=0):
 stamp=str(datetime.now(timezone.utc).timestamp()); token=f"settle-{stamp}"; guest_token=f"gst_settle-{stamp}"
 with _factory(engine).begin() as db:
  user=User(name="Settler",email=f"settle-{stamp}@test"); db.add(user); db.flush(); trip=Trip(name="Settle",destination="Chicago",created_by_user_id=user.id); db.add(trip); db.flush(); member=TripMembership(trip_id=trip.id,user_id=user.id,status="joined"); guest=TripMembership(trip_id=trip.id,guest_display_name="Guest",join_method="invite_guest",status="joined"); extras=[TripMembership(trip_id=trip.id,guest_display_name=f"Extra{i}",join_method="invite_guest",status="joined") for i in range(extra)]; plan=Plan(trip_id=trip.id); db.add_all((member,guest,*extras,plan)); db.flush(); gs=GuestSession(membership_id=guest.id,trip_id=trip.id,token_hash=auth.token_hash(guest_token),expires_at=datetime.now(timezone.utc)+timedelta(days=1)); db.add_all((AuthSession(user_id=user.id,token_hash=auth.token_hash(token),expires_at=datetime.now(timezone.utc)+timedelta(days=1)),gs)); item=PlanItem(plan_id=plan.id,day_index=1,day_date=date(2026,10,8),start_hour=12,duration_min=60,title="Round item",place="Chicago",settledness="settled" if kind=="reopen" else "touched"); db.add(item); db.flush(); round_=DecisionRound(plan_item_id=item.id,kind=kind,options=_opts(),deadline=datetime.now(timezone.utc)+timedelta(days=1),status="open",electorate_count=2+extra); db.add(round_); db.flush(); _trips.append((trip.id,user.id)); return Seed(trip.id,plan.id,item.id,round_.id,user.id,member.id,token,guest.id,guest_token,gs.id)
@pytest.fixture(autouse=True)
def cleanup(test_engine):
 _trips.clear(); yield
 with _factory(test_engine).begin() as db:
  for trip_id,user_id in _trips:
   plans=select(Plan.id).where(Plan.trip_id==trip_id); items=select(PlanItem.id).where(PlanItem.plan_id.in_(plans)); rounds=select(DecisionRound.id).where(DecisionRound.plan_item_id.in_(items)); db.query(MutationIdempotency).filter(MutationIdempotency.trip_id==trip_id).delete(synchronize_session=False); db.query(UpdateNotice).filter(UpdateNotice.trip_id==trip_id).delete(synchronize_session=False); db.query(Vote).filter(Vote.round_id.in_(rounds)).delete(synchronize_session=False); db.query(DecisionRound).filter(DecisionRound.plan_item_id.in_(items)).delete(synchronize_session=False); db.query(PlanChange).filter(PlanChange.plan_id.in_(plans)).delete(synchronize_session=False); db.query(GuestSession).filter(GuestSession.trip_id==trip_id).delete(synchronize_session=False); db.query(PlanItem).filter(PlanItem.plan_id.in_(plans)).delete(synchronize_session=False); db.query(Plan).filter(Plan.trip_id==trip_id).delete(synchronize_session=False); db.query(TripMembership).filter(TripMembership.trip_id==trip_id).delete(synchronize_session=False); db.query(Trip).filter(Trip.id==trip_id).delete(synchronize_session=False); db.query(AuthSession).filter(AuthSession.user_id==user_id).delete(synchronize_session=False); db.query(User).filter(User.id==user_id).delete(synchronize_session=False)
def _exec(engine,s,key="settle",revision=0,guest=False):
 scope=PreliminaryActorScope(trip_id=s.trip_id,membership_id=s.guest_id if guest else s.membership_id,credential_kind="guest" if guest else "account",expected_user_id=None if guest else s.account_id); token=s.guest_token if guest else s.token
 return protected.execute_protected_round_settlement(session_factory=_factory(engine),scope=scope,credential=CredentialProof.from_bearer_token(token),command=protected.ProtectedRoundSettlement(s.round_id,key,revision),keyring=protected.StaticHmacKeyring(HmacSigningKey("v1",b"settle-test-key")))
def test_round_settlement_adapter_exposes_a_versioned_operation(): assert protected.ROUND_SETTLEMENT_OPERATION=="round.settle.v1"
def test_normal_majority_settlement_replay_and_closed_noop(test_engine):
 s=_seed(test_engine)
 with _factory(test_engine).begin() as db: db.add(Vote(round_id=s.round_id,trip_membership_id=s.membership_id,option_id="requested"))
 first=_exec(test_engine,s); replay=_exec(test_engine,s)
 assert first.plan_revision==1 and replay.replayed and replay.payload==first.payload
 closed=_exec(test_engine,s,"closed",1); assert closed.plan_revision==1 and not closed.replayed
 with _factory(test_engine)() as db: assert db.get(DecisionRound,s.round_id).status=="closed" and db.get(PlanItem,s.item_id).title=="Changed item" and db.get(Plan,s.plan_id).revision==1 and db.query(PlanChange).filter(PlanChange.plan_id==s.plan_id).count()==db.query(UpdateNotice).filter(UpdateNotice.trip_id==s.trip_id).count()==1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id==s.trip_id).count()==2
@pytest.mark.parametrize("kind,votes,winner,title",[("normal",["requested","remove"],"keep","Round item"),("reopen",["requested","keep"],"keep","Round item"),("reopen",["requested","requested","requested"],"requested","Changed item"),("normal",[],"keep","Round item"),("normal",["remove"],"remove","Round item")])
def test_winner_rules_ties_reopen_zero_vote_and_removal(test_engine,kind,votes,winner,title):
 s=_seed(test_engine,kind,extra=2 if kind=="reopen" else 0)
 with _factory(test_engine).begin() as db:
  members=db.scalars(select(TripMembership).where(TripMembership.trip_id==s.trip_id).order_by(TripMembership.id)).all()
  for m,opt in zip(members,votes): db.add(Vote(round_id=s.round_id,trip_membership_id=m.id,option_id=opt))
 result=_exec(test_engine,s); assert result.payload["winning_option_id"]==winner
 with _factory(test_engine)() as db: assert db.get(PlanItem,s.item_id).title==title and db.get(Plan,s.plan_id).revision==1 and (winner!="remove" or db.get(PlanItem,s.item_id).settledness=="removed")
def test_stale_key_conflict_cross_trip_and_failure_rollback(test_engine,monkeypatch):
 s=_seed(test_engine); _exec(test_engine,s,"same")
 with pytest.raises(IdempotencyKeyReused): _exec(test_engine,s,"same",1)
 with pytest.raises(StalePlanRevision): _exec(test_engine,s,"stale",0)
 s2=_seed(test_engine)
 original=protected.DurableCommandKernel._checkpoint
 monkeypatch.setattr(protected.DurableCommandKernel,"_checkpoint",lambda self,stage: (_ for _ in ()).throw(RuntimeError("fail")) if stage=="after_domain_effect" else original(self,stage))
 with pytest.raises(RuntimeError): _exec(test_engine,s2)
 with _factory(test_engine)() as db: assert db.get(DecisionRound,s2.round_id).status=="open" and db.get(Plan,s2.plan_id).revision==0 and db.query(PlanChange).filter(PlanChange.plan_id==s2.plan_id).count()==db.query(UpdateNotice).filter(UpdateNotice.trip_id==s2.trip_id).count()==db.query(MutationIdempotency).filter(MutationIdempotency.trip_id==s2.trip_id).count()==0
def test_guest_route_gate_and_revocation(test_engine,monkeypatch):
 s=_seed(test_engine); f=_factory(test_engine); monkeypatch.setattr(api,"SessionLocal",f); monkeypatch.setattr(db_session,"SessionLocal",f); headers={"Authorization":f"Bearer {s.guest_token}","X-Trip-Id":s.trip_id,"Idempotency-Key":"guest","X-Expected-Plan-Revision":"0"}
 with TestClient(api.app) as c: legacy=c.post(f"/api/rounds/{s.round_id}/settle",headers={"Authorization":f"Bearer {s.guest_token}","X-Trip-Id":s.trip_id,"X-Expected-Plan-Revision":"bad"})
 assert legacy.status_code==200
 s=_seed(test_engine); headers={"Authorization":f"Bearer {s.guest_token}","X-Trip-Id":s.trip_id,"Idempotency-Key":"guest","X-Expected-Plan-Revision":"0"}; monkeypatch.setenv("CADENSY_B2C_PROTECTED_ROUND_SETTLEMENT_WRITES","1"); monkeypatch.setenv("CADENSY_B2C_HMAC_KEY","route-key")
 with TestClient(api.app) as c: first=c.post(f"/api/rounds/{s.round_id}/settle",headers=headers); replay=c.post(f"/api/rounds/{s.round_id}/settle",headers=headers)
 assert first.status_code==replay.status_code==200 and first.json()==replay.json()
 with f.begin() as db: db.get(GuestSession,s.guest_session_id).revoked_at=datetime.now(timezone.utc)
 with TestClient(api.app) as c: assert c.post(f"/api/rounds/{s.round_id}/settle",headers={**headers,"Idempotency-Key":"revoked","X-Expected-Plan-Revision":"1"}).status_code==401
 with f.begin() as db: db.get(GuestSession,s.guest_session_id).revoked_at=None; db.get(TripMembership,s.guest_id).status="removed"
 with TestClient(api.app) as c: assert c.post(f"/api/rounds/{s.round_id}/settle",headers={**headers,"Idempotency-Key":"removed","X-Expected-Plan-Revision":"1"}).status_code in {401,403}
def test_settlement_then_final_vote_serializes_at_root_and_vote_is_stale(test_engine,monkeypatch):
 s=_seed(test_engine)
 with _factory(test_engine).begin() as db: db.add(Vote(round_id=s.round_id,trip_membership_id=s.membership_id,option_id="requested"))
 entered=Event(); release=Event(); first_lock=Event(); second_reached=Event(); second_acquired=Event(); out=Queue(); pids={}; original=protected.orch.settle_round
 def paused(*args): value=original(*args); entered.set(); assert release.wait(5); return value
 monkeypatch.setattr(protected.orch,"settle_round",paused)
 def root(statement):
  normalized=" ".join(statement.lower().split()); return "from trip" in normalized and "trip_membership" not in normalized and "for update" in normalized
 def pid(conn):
  raw=conn.connection; return getattr(raw,"driver_connection",raw).info.backend_pid
 def before(conn,_c,statement,_p,_ctx,_many):
  if root(statement):
   if current_thread().name=="settlement-first": pids["settlement"]=pid(conn)
   elif current_thread().name=="final-vote-second": pids["vote"]=pid(conn); second_reached.set()
 def after(_conn,_c,statement,_p,_ctx,_many):
  if root(statement):
   if current_thread().name=="settlement-first": first_lock.set()
   elif current_thread().name=="final-vote-second": second_acquired.set()
 def settle():
  try: out.put(_exec(test_engine,s,"settle"))
  except BaseException as e: out.put(e)
 def vote():
  try:
   scope=PreliminaryActorScope(trip_id=s.trip_id,membership_id=s.guest_id,credential_kind="guest"); out.put(protected_vote.execute_protected_vote(session_factory=_factory(test_engine),scope=scope,credential=CredentialProof.from_bearer_token(s.guest_token),command=protected_vote.ProtectedVote(s.round_id,"vote",0,"requested"),keyring=protected_vote.StaticHmacKeyring(HmacSigningKey("v1",b"settle-test-key"))))
  except BaseException as e: out.put(e)
 event.listen(test_engine,"before_cursor_execute",before); event.listen(test_engine,"after_cursor_execute",after)
 try:
  a=Thread(target=settle,name="settlement-first",daemon=True); a.start(); assert first_lock.wait(5) and entered.wait(5)
  b=Thread(target=vote,name="final-vote-second",daemon=True); b.start(); assert second_reached.wait(5) and not second_acquired.is_set() and pids["settlement"]!=pids["vote"]
  observed=False; deadline=monotonic()+5
  while monotonic()<deadline:
   with _factory(test_engine)() as db: blockers=db.scalar(text("select pg_blocking_pids(:pid)"),{"pid":pids["vote"]})
   if pids["settlement"] in (blockers or []): observed=True; break
  assert observed
 finally:
  release.set(); a.join(5); b.join(5); event.remove(test_engine,"before_cursor_execute",before); event.remove(test_engine,"after_cursor_execute",after)
 assert not a.is_alive() and not b.is_alive() and second_acquired.is_set(); results=[out.get_nowait(),out.get_nowait()]
 assert any(getattr(x,"plan_revision",None)==1 for x in results) and any(isinstance(x,StalePlanRevision) for x in results)
 with _factory(test_engine)() as db: assert db.get(DecisionRound,s.round_id).status=="closed" and db.get(DecisionRound,s.round_id).winning_option_id=="requested" and db.get(PlanItem,s.item_id).title=="Changed item" and db.get(Plan,s.plan_id).revision==1 and db.query(Vote).filter(Vote.round_id==s.round_id).count()==1 and db.query(PlanChange).filter(PlanChange.plan_id==s.plan_id).count()==db.query(UpdateNotice).filter(UpdateNotice.trip_id==s.trip_id).count()==db.query(MutationIdempotency).filter(MutationIdempotency.trip_id==s.trip_id).count()==1

def test_cross_trip_round_is_plan_scoped_before_lock_and_has_no_effect(test_engine):
 source=_seed(test_engine); foreign=_seed(test_engine); probes=[]
 def capture(_conn,_c,statement,parameters,_ctx,_many):
  normalized=" ".join(statement.lower().split())
  if "decision_round" in normalized and "for update" in normalized and foreign.round_id in str(parameters): probes.append(normalized)
 event.listen(test_engine,"before_cursor_execute",capture)
 try:
  scope=PreliminaryActorScope(trip_id=source.trip_id,membership_id=source.membership_id,credential_kind="account",expected_user_id=source.account_id)
  with pytest.raises(protected.RoundSettlementRoundMissing): protected.execute_protected_round_settlement(session_factory=_factory(test_engine),scope=scope,credential=CredentialProof.from_bearer_token(source.token),command=protected.ProtectedRoundSettlement(foreign.round_id,"foreign",0),keyring=protected.StaticHmacKeyring(HmacSigningKey("v1",b"settle-test-key")))
 finally: event.remove(test_engine,"before_cursor_execute",capture)
 assert probes and "plan_item.plan_id" in probes[0]
 with _factory(test_engine)() as db: assert db.get(DecisionRound,foreign.round_id).status=="open" and db.get(Plan,foreign.plan_id).revision==db.get(Plan,source.plan_id).revision==0 and db.query(Vote).filter(Vote.round_id==foreign.round_id).count()==db.query(PlanChange).count()==db.query(UpdateNotice).count()==db.query(MutationIdempotency).count()==0

def test_final_vote_then_settlement_has_postgresql_trip_block_and_fresh_noop(test_engine,monkeypatch):
 s=_seed(test_engine)
 with _factory(test_engine).begin() as db: db.add(Vote(round_id=s.round_id,trip_membership_id=s.membership_id,option_id="requested"))
 entered=Event(); release=Event(); first_lock=Event(); second_reached=Event(); second_acquired=Event(); pids={}; outcomes=Queue(); original=protected_vote.orch.settle_round
 def paused(*args): value=original(*args); entered.set(); assert release.wait(5); return value
 monkeypatch.setattr(protected_vote.orch,"settle_round",paused)
 def root(statement):
  normalized=" ".join(statement.lower().split()); return "from trip" in normalized and "trip_membership" not in normalized and "for update" in normalized
 def pid(conn):
  raw=conn.connection; return getattr(raw,"driver_connection",raw).info.backend_pid
 def before(conn,_c,statement,_p,_ctx,_many):
  if root(statement):
   if current_thread().name=="final-vote": pids["vote"]=pid(conn)
   if current_thread().name=="standalone-settle": pids["settle"]=pid(conn); second_reached.set()
 def after(_conn,_c,statement,_p,_ctx,_many):
  if root(statement):
   if current_thread().name=="final-vote": first_lock.set()
   if current_thread().name=="standalone-settle": second_acquired.set()
 def vote():
  try:
   scope=PreliminaryActorScope(trip_id=s.trip_id,membership_id=s.guest_id,credential_kind="guest"); outcomes.put(protected_vote.execute_protected_vote(session_factory=_factory(test_engine),scope=scope,credential=CredentialProof.from_bearer_token(s.guest_token),command=protected_vote.ProtectedVote(s.round_id,"vote",0,"requested"),keyring=protected_vote.StaticHmacKeyring(HmacSigningKey("v1",b"settle-test-key"))))
  except BaseException as exc: outcomes.put(exc)
 def settle():
  try: outcomes.put(_exec(test_engine,s,"stale-settle"))
  except BaseException as exc: outcomes.put(exc)
 event.listen(test_engine,"before_cursor_execute",before); event.listen(test_engine,"after_cursor_execute",after)
 try:
  first=Thread(target=vote,name="final-vote",daemon=True); first.start(); assert first_lock.wait(5) and entered.wait(5)
  second=Thread(target=settle,name="standalone-settle",daemon=True); second.start(); assert second_reached.wait(5) and not second_acquired.is_set()
  observed=False; deadline=monotonic()+5
  while monotonic()<deadline:
   with _factory(test_engine)() as db: blockers=db.scalar(text("select pg_blocking_pids(:pid)"),{"pid":pids["settle"]})
   if pids["vote"] in (blockers or []): observed=True; break
  assert observed
  release.set(); first.join(5); second.join(5)
 finally:
  event.remove(test_engine,"before_cursor_execute",before); event.remove(test_engine,"after_cursor_execute",after)
 results=[outcomes.get_nowait(),outcomes.get_nowait()]; assert any(getattr(x,"plan_revision",None)==1 for x in results) and any(isinstance(x,StalePlanRevision) for x in results)
 noop=_exec(test_engine,s,"fresh-noop",1); assert noop.plan_revision==1 and noop.payload["status"]=="closed"
 with _factory(test_engine)() as db: assert db.query(PlanChange).filter(PlanChange.plan_id==s.plan_id).count()==db.query(UpdateNotice).filter(UpdateNotice.trip_id==s.trip_id).count()==1 and db.query(MutationIdempotency).filter(MutationIdempotency.trip_id==s.trip_id).count()==2
