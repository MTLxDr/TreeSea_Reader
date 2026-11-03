from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime, timezone
from uuid import uuid4
from .run_tracking import RunTracker
from pathlib import Path

from .catalog import ROOT
from .recognition import save_result


def map_signature(result):
    """Compare template coordinates, never screenshot pixels or zoom."""
    if not result or not result.get("template_id") or not result.get("nodes"):
        return None
    edges = tuple(sorted(tuple(sorted((tuple(a), tuple(b)))) for a, b in result.get("edges", [])))
    return (result.get("floor"), result["template_id"],
            tuple(sorted(tuple(n["slot"]) for n in result["nodes"])), edges,
            tuple(result.get("start_slot") or ()),
            tuple(sorted(map(tuple, result.get("terminal_slots", [])))))


def reliable_node(node):
    return bool(node.get("manual") or node.get("evidence") in {"template_fixed_exit","template_fixed_node"} or
                (node.get("evidence") == "ocr_label" and node.get("confidence", 0) >= .75))


class Session:
    """Reversible recognition updates; manual corrections survive same-map reads."""
    def __init__(self, path=ROOT / "data/sessions/current.json"):
        self.path = Path(path)
        self.current = None
        self.run = self._new_run_info("created")
        self.run_tracker = RunTracker()
        self.transition_message = ""
        self.just_started = False
        self.inventories = {kind: {"items": [], "pages": [], "complete": False} for kind in ("relics", "parts", "operators")}
        self.history = []
        self.exploration = {'zone_serial':0,'events':[],'initial_residents':[],'last_event':None}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.run = saved.get("run") or self.run
                self.current = saved.get("current")
                self.history = saved.get("history", [])[-20:]
                self.inventories.update(saved.get("inventories", {}))
                self.exploration.update(saved.get('exploration',{}))
                for event in self.exploration['events']:
                    event.setdefault('occurrence_id',uuid4().hex)
            except (OSError, ValueError, TypeError):
                # Keep a corrupt file for inspection; do not silently overwrite it.
                raise ValueError(f"本局存档无法读取：{self.path}。请保留文件并使用新存档。")

    def persist(self):
        save_result({"schema_version": 2, "run":self.run, "current": self.current, "inventories": self.inventories, "history": self.history,
                     'exploration':self.exploration}, self.path)

    def apply(self, result):
        if result.get("page_type") != "map" or result.get("needs_review"):
            return False
        self.just_started = False
        decision, self.transition_message = self.run_tracker.assess(self.current, result)
        if decision == 'wait':
            return False
        incoming = deepcopy(result)
        incoming.pop('_stable_observations',None)
        incoming.pop('_new_run_candidate',None)
        incoming.pop('_continuity_nodes',None)
        if decision == 'new':
            self._push()
            self._clear_run('automatic_first_floor_start')
            self.just_started = True
        incoming['run_id'] = self.run['id']
        old = self.current
        same_map = map_signature(old) is not None and map_signature(old) == map_signature(incoming)
        if not self.just_started:self._push()
        if not same_map:
            self.exploration['zone_serial']+=1;self.exploration['initial_residents']=[];self.exploration['last_event']=None
            if incoming.get('floor')=='treehole' and (old or {}).get('floor') in (3,4,5):
                self.exploration['entrance_floor']=old['floor']
                self.exploration.pop('entrance_floor_manual',None)
                self.exploration.pop('entrance_floor_before_manual',None)
        incoming["map_continuity"] = {"same_map": same_map,
            "method": "floor_template_slots_edges_start_terminals"}
        if not same_map and incoming.get('position_evidence') == '人物标记':
            incoming['last_confirmed_slot'] = deepcopy(incoming.get('current_slot'))
        if same_map:
            for name, value in old.get("fields", {}).items():
                if value.get("manual"):
                    retained=deepcopy(value)
                    observed=incoming.get('fields',{}).get(name)
                    if observed and observed.get('value') is not None:retained['manual_previous']=deepcopy(observed)
                    incoming.setdefault("fields", {})[name] = retained
            self._retain_nodes(old, incoming)
            self._track_arrival(old, incoming)
            if old.get('position_manual'):
                incoming.update(current_slot=deepcopy(old.get('current_slot')),position_manual=True,
                    position_before_manual=deepcopy(old.get('position_before_manual')),
                    position_initial_before_manual=deepcopy(old.get('position_initial_before_manual',[])),
                    position_nodes_before_manual=deepcopy(old.get('position_nodes_before_manual',{})),
                    position_evidence='手动指定当前位置',at_start=old.get('current_slot')==incoming.get('start_slot'))
            old_side, new_side = old.get("side_map", {}), incoming.get("side_map", {})
            if old_side.get("nodes") and new_side.get("status") == "not_observed":
                incoming["side_map"] = deepcopy(old_side)
                incoming["side_map"]["observed_in_frame"] = False
                incoming.setdefault("notices", []).append("本次未观察到右侧小图，保留历史小图；不在本次原图上叠加旧坐标。")
            if map_signature(old_side) is not None and map_signature(old_side) == map_signature(new_side):
                self._retain_nodes(old_side, new_side)
        self._update_conflicts(incoming)
        from .resident_tracking import update_resident_constraints
        if incoming.get('at_start') and not self.exploration['initial_residents']:
            self.exploration['initial_residents']=[n['slot'] for n in incoming['nodes'] if n.get('resident')]
        update_resident_constraints(incoming,self.exploration['initial_residents'])
        self.current = incoming
        self.merge_recent(incoming)
        self.persist()
        return True

    def _track_arrival(self, old, incoming):
        slot = incoming.get('current_slot')
        previous = old.get('last_confirmed_slot')
        if previous is None and old.get('position_evidence') == '人物标记':
            previous = old.get('current_slot')
        incoming['last_confirmed_slot'] = deepcopy(previous)
        if slot is None or incoming.get('position_evidence') != '人物标记' or old.get('position_manual'):
            return
        incoming['last_confirmed_slot'] = deepcopy(slot)
        if previous is None or slot == previous or incoming.get('at_start'):
            return
        node = next((n for n in incoming['nodes'] if n['slot'] == slot), {})
        self.exploration['last_event'] = None
        self._append_action(incoming, 'enter_node', slot=slot, from_slot=previous,
                            node_type=node.get('type'))

    def _append_action(self, observation, kind, **fields):
        actions = self.exploration.setdefault('actions', [])
        actions.append(dict(type=kind, run_id=self.run['id'], zone=self.exploration['zone_serial'],
                            floor=observation.get('floor'), captured_at=observation.get('captured_at'), **fields))

    def _push(self):
        self.history.append(deepcopy({"snapshot_version": 2, "run": self.run, "map": self.current, "inventories": self.inventories,'exploration':self.exploration}))
        self.history = self.history[-20:]

    @staticmethod
    def _retain_nodes(old, incoming):
        previous_nodes={tuple(n['slot']):n for n in old.get('nodes',[])}
        corrections = {tuple(n["slot"]): n for n in old.get("nodes", []) if reliable_node(n)}
        hidden = {"unobserved", "unknown-ferocity", "unknown-mystery"}
        for node in incoming.get("nodes", []):
            previous=previous_nodes.get(tuple(node['slot']),{})
            # Occupancy history survives even when the hidden node has no OCR label.
            if previous.get('resident_visited') or previous.get('resident') or previous.get('resident_initial'):
                node['resident_visited']=True
            if previous.get('resident_initial'):
                node['resident_initial']=True;node['underlying_type']='empty'
            correction = corrections.get(tuple(node["slot"]))
            if not correction:
                continue
            previous_hidden = correction["type"] in hidden
            incoming_hidden = node["type"] in hidden
            conflict = (not correction.get("manual") and reliable_node(node)
                        and correction["type"] != node["type"]
                        and previous_hidden == incoming_hidden and node["type"] != "unobserved")
            retain = (correction.get("manual") or conflict or not reliable_node(node)
                      or (not previous_hidden and incoming_hidden)
                      or (correction["type"] == node["type"]
                          and correction.get("confidence", 0) > node.get("confidence", 0)))
            previous_conflict = correction.get("observation_conflict")
            if conflict:
                previous_conflict = {"previous": correction["label"], "incoming": node["label"]}
            if retain:
                observed={k:deepcopy(node[k]) for k in ('type','label','confidence','evidence','manual') if k in node}
                # New pixel coordinates and distance always belong to the new frame.
                for key in ("type", "label", "confidence", "evidence", "manual", "probabilities",
                            "fixed_by_template", "observed_type", "manual_previous"):
                    if key in correction:
                        node[key] = deepcopy(correction[key])
                    else:
                        node.pop(key, None)
                if correction.get("manual"):node["manual_previous"]=observed
                node["retained_from_previous"] = True
            if previous_conflict and not correction.get("manual"):
                node["observation_conflict"] = deepcopy(previous_conflict)

    @staticmethod
    def _update_conflicts(region):
        conflicts = [{"slot": n["slot"], **n["observation_conflict"]}
                     for n in region.get("nodes", []) if n.get("observation_conflict")]
        region["node_conflicts"] = conflicts
        region["needs_node_review"] = bool(conflicts)

    def edit_node(self, slot, kind, label, probabilities=None, region="main"):
        if self.current is None:
            return
        self._push()
        target = self.current if region == "main" else self.current.get("side_map", {})
        for node in target.get("nodes", []):
            if tuple(node["slot"]) == tuple(slot):
                if not node.get('manual'):
                    node['manual_previous']={k:deepcopy(node[k]) for k in ('type','label','confidence','evidence','manual') if k in node}
                node.update(type=kind, label=label, confidence=1.0, evidence="manual", manual=True,
                            probabilities=deepcopy(probabilities))
                node.pop("observation_conflict", None)
        self._update_conflicts(target)
        self.persist()

    def edit_field(self, name, value):
        if self.current is None:
            return
        self._push()
        previous=deepcopy(self.current.get('fields',{}).get(name))
        if previous and previous.get('manual'):previous=previous.get('manual_previous')
        self.current.setdefault("fields", {})[name] = {
            "manual_previous":previous,"value": value, "confidence": 1.0, "source": "manual", "manual": True, "raw": None}
        self.persist()

    def undo(self):
        if not self.history:
            return False
        previous = self.history.pop()
        if isinstance(previous, dict) and previous.get("snapshot_version") == 2:
            self.run=previous.get("run",self.run)
            self.current, self.inventories = previous["map"], previous["inventories"]
            self.exploration=previous.get('exploration',{'zone_serial':0,'events':[],'initial_residents':[],'last_event':None})
        else:
            self.current = previous
        self.run_tracker=RunTracker()
        self.transition_message=""
        self.persist()
        return True

    @staticmethod
    def _new_run_info(reason):
        return {"id":uuid4().hex,"started_at":datetime.now(timezone.utc).isoformat(),"reason":reason}

    def reset(self):
        self._push()
        self._clear_run('manual')
        self.persist()

    def _clear_run(self, reason):
        self.current = None
        self.run = self._new_run_info(reason)
        self.run_tracker = RunTracker()
        self.exploration={'zone_serial':0,'events':[],'initial_residents':[],'last_event':None}
        self.inventories = {kind: {"items": [], "pages": [], "complete": False} for kind in self.inventories}

    def merge_inventory(self, result, entries):
        if result.get("page_type") != "inventory" or not entries:
            return False
        kind = result["inventory_kind"]
        bucket = self.inventories[kind]
        self._push()
        if kind == 'parts' and result.get('coverage'):
            bucket['visible_cards'] = deepcopy(entries)
            bucket.setdefault('page_fields', {}).update(deepcopy(result.get('page_fields', {})))
            grouped = {}
            for row in entries:
                grouped.setdefault(row['id'], []).append(row)
            entries = []
            for copies in grouped.values():
                entry = deepcopy(copies[0])
                for key in ('valuation', 'uses'):
                    observed = {c['fields'].get(key, {}).get('value') for c in copies}
                    if len(observed) > 1:
                        entry['fields'][key] = dict(value=None, source='different_visible_instances', confidence=0)
                entry['visible_instances'] = len(copies)
                entries.append(entry)
        by_id = {row["id"]: row for row in bucket["items"]}
        for entry in entries:
            old = by_id.get(entry["id"])
            incoming = deepcopy(entry)
            incoming["confirmed"] = True
            incoming["last_seen"] = result["captured_at"]
            if old:
                if old.get("excluded"):
                    continue  # A manual removal must not be undone by a scan.
                for key, value in old.get("fields", {}).items():
                    observed = incoming['fields'].get(key, {})
                    if value.get("manual") or (
                            observed.get('value') is None and observed.get('source') != 'different_visible_instances'):
                        retained=deepcopy(value)
                        if value.get('manual') and observed and observed.get('value') is not None and observed.get('confidence',0)>=.9:
                            retained['manual_previous']=deepcopy(observed)
                        incoming["fields"][key] = retained
            incoming["observations"] = ((old or {}).get("observations", []) + [
                {"page": result["page_fingerprint"], "source": result.get("source"), "captured_at": result["captured_at"]}])[-20:]
            if old:
                old.update(incoming)
            else:
                bucket["items"].append(incoming)
                by_id[incoming["id"]] = incoming
        if result["page_fingerprint"] not in bucket["pages"]:
            bucket["pages"].append(result["page_fingerprint"])
        bucket["complete"] = False
        self.persist()
        return True

    def manual_inventory(self, kind, item, fields=None, excluded=False):
        self._push()
        bucket = self.inventories[kind]
        existing = next((row for row in bucket["items"] if row["id"] == item["id"]), None)
        if existing is None:
            existing = dict(deepcopy(item), fields={}, manual=True, confirmed=True)
            bucket["items"].append(existing)
        existing["excluded"] = excluded
        for key, value in (fields or {}).items():
            previous=deepcopy(existing.get('fields',{}).get(key))
            if previous and previous.get('manual'):previous=previous.get('manual_previous')
            existing.setdefault("fields", {})[key] = {"manual_previous":previous,"value": value, "manual": True, "source": "manual", "confidence": 1.}
        bucket["complete"] = False
        self.persist()

    def complete_inventory(self, kind, complete):
        self._push()
        self.inventories[kind]["complete"] = bool(complete)
        self.persist()

    def record_event(self,event):
        identity=event['event_id']
        manual=(event.get('source') or {}).get('method')=='manual'
        if self.exploration.get('last_event')==identity and not manual:return False
        from .exploration import ExplorationCatalog
        info=ExplorationCatalog.shared().event_info(identity)
        repeatable=event.get('repeatable',info.get('repeatable',False))
        if not repeatable and any(e['id']==identity for e in self.exploration['events']):return False
        self._push();self.exploration['last_event']=identity
        self.exploration['events'].append({'id':identity,'name':event['event_name'],
            'occurrence_id':uuid4().hex,'run_id':self.run['id'],
            'zone':self.exploration['zone_serial'],'floor':(self.current or {}).get('floor'),
            'repeatable':repeatable,'captured_at':event.get('captured_at'),'source':event.get('source'),
            'evidence':deepcopy(event.get('event_evidence'))})
        if not manual:
            self._append_action(dict(event, floor=(self.current or {}).get('floor')), 'enter_event',
                                event_id=identity, event_name=event['event_name'],
                                evidence=event.get('event_evidence'),
                                occurrence_id=self.exploration['events'][-1]['occurrence_id'])
        self.persist();return True

    def remove_event(self,occurrence_id):
        rows=self.exploration['events']
        if not any(e.get('occurrence_id')==occurrence_id for e in rows):return False
        self._push()
        self.exploration['events']=[e for e in rows if e.get('occurrence_id')!=occurrence_id]
        if 'actions' in self.exploration:
            self.exploration['actions']=[a for a in self.exploration['actions'] if a.get('occurrence_id')!=occurrence_id]
        # last_event intentionally remains: an unchanged event page must not undo deletion.
        self.persist();return True

    def clear_manual(self):
        self._push();count=0
        for region in [self.current or {},(self.current or {}).get('side_map',{})]:
            for node in region.get('nodes',[]):
                if node.get('manual'):
                    original=node.pop('manual_previous',None)
                    if original:node.update(original)
                    else:node.update(type='unobserved',label='未识别',confidence=0.,evidence='template_only',manual=False)
                    node.pop('probabilities',None);count+=1
            for key,value in list(region.get('fields',{}).items()):
                if value.get('manual'):
                    original=value.get('manual_previous')
                    if original:region['fields'][key]=original
                    else:region['fields'].pop(key)
                    count+=1
            if region.pop('position_manual',False):
                region['current_slot']=region.pop('position_before_manual',None)
                initial=region.pop('position_initial_before_manual',[])
                previous=region.pop('position_nodes_before_manual',{})
                if region is self.current:self.exploration['initial_residents']=initial
                for node in region.get('nodes',[]):
                    snapshot=previous.get(','.join(map(str,node['slot'])))
                    if snapshot and node.get('resident_initial'):
                        for key in ('type','label','evidence','confidence','resident_initial','underlying_type','resident_visited','excluded_types'):
                            if key in snapshot:node[key]=deepcopy(snapshot[key])
                            else:node.pop(key,None)
                region['at_start']=region.get('current_slot') is not None and region.get('current_slot')==region.get('start_slot')
                count+=1
            self._update_conflicts(region)
        for bucket in self.inventories.values():
            rows=[]
            for row in bucket['items']:
                if row.get('manual') and not row.get('observations'):
                    count+=1;continue
                if row.pop('excluded',False):count+=1
                for key,value in list(row.get('fields',{}).items()):
                    if value.get('manual'):
                        original=value.get('manual_previous')
                        if original:row['fields'][key]=original
                        else:row['fields'].pop(key)
                        count+=1
                rows.append(row)
            bucket.update(items=rows,complete=False)
        events=self.exploration['events']
        self.exploration['events']=[e for e in events if (e.get('source') or {}).get('method')!='manual']
        count+=len(events)-len(self.exploration['events'])
        if self.exploration.pop('entrance_floor_manual',False):
            previous=self.exploration.pop('entrance_floor_before_manual',None)
            if previous is None:self.exploration.pop('entrance_floor',None)
            else:self.exploration['entrance_floor']=previous
            count+=1
        self.run_tracker=RunTracker()
        if self.current:
            from .resident_tracking import update_resident_constraints
            update_resident_constraints(self.current,self.exploration['initial_residents'])
        self.persist();return count

    def set_entrance_floor(self,value):
        self._push()
        if not self.exploration.get('entrance_floor_manual'):
            self.exploration['entrance_floor_before_manual']=self.exploration.get('entrance_floor')
        self.exploration.update(entrance_floor=value,entrance_floor_manual=True)
        self.persist()

    def set_position(self,slot):
        if not self.current or not any(n['slot']==list(slot) for n in self.current.get('nodes',[])):return
        self._push()
        from .resident_tracking import infer_position,update_resident_constraints
        if not self.current.get('position_manual'):
            self.current['position_before_manual']=deepcopy(self.current.get('current_slot'))
            self.current['position_initial_before_manual']=deepcopy(self.exploration['initial_residents'])
            self.current['position_nodes_before_manual']={','.join(map(str,n['slot'])):{k:deepcopy(n[k]) for k in
                ('type','label','evidence','confidence','resident_initial','underlying_type','resident_visited','excluded_types') if k in n} for n in self.current['nodes']}
        infer_position(self.current,list(slot));self.current['position_manual']=True
        if self.current.get('at_start') and not self.exploration['initial_residents']:
            self.exploration['initial_residents']=[n['slot'] for n in self.current['nodes'] if n.get('resident')]
        update_resident_constraints(self.current,self.exploration['initial_residents']);self.persist()

    def merge_recent(self,result):
        changed=0;bucket=self.inventories['relics'];known={r['id']:r for r in bucket['items']}
        for entry in result.get('recent_relics',[]):
            if entry.get('needs_review'):continue
            previous=known.get(entry['id'])
            if previous:continue
            row=deepcopy(entry);row.update(confirmed=True,confirmed_by='recent_acquisition_strip',run_id=self.run['id'])
            row['observations']=[{'source':result.get('source'),'captured_at':result.get('captured_at'),'method':'recent_icon','match_score':entry['match_score']}]
            bucket['items'].append(row);known[row['id']]=row;changed+=1
        if changed:bucket['complete']=False
        return changed

    def auto_inventory(self,result):
        if result.get('ownership_risk') or result.get('context_detected')!=result.get('inventory_kind'):return False
        threshold = .86 if result.get('coverage') else .90
        entries=[deepcopy(r) for r in result.get('entries',[]) if not r.get('needs_review')
                 and (r.get('confidence',0)>=threshold or (result.get('coverage') and r.get('corroborated')))]
        for entry in entries:
            entry['confirmed_by']='live_owned_page'
            entry['fields']={k:v for k,v in entry.get('fields',{}).items()
                if v.get('value') is not None and v.get('confidence',0) >=
                (.78 if result.get('coverage') and v.get('source') == 'card_region_ocr' else .90)}
        bucket=self.inventories[result['inventory_kind']]
        if bucket.get('last_live_fingerprint')==result['page_fingerprint']:return False
        changed=self.merge_inventory(result,entries)
        if changed:
            bucket['last_live_fingerprint']=result['page_fingerprint']
            bucket['last_visible_items']=[r['id'] for r in entries]
            bucket['last_observed_at']=result['captured_at'];self.persist()
        return changed
