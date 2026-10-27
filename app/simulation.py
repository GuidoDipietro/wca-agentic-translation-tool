"""Copy of a real download with three local gaps to test generation and review."""
import io
import json
import copy

from core import Catalog, ReviewError, forms
from locales import yaml_load, assign
from weblate import Client


class SimulationClient(Client):
    is_simulation = True

    def download(self):
        payload = copy.deepcopy(super().download())
        target = Catalog(payload['target'])
        available = [(k, e) for k, e in target.by_key.items() if all(v.strip() for v in forms(e)) and 'fuzzy' not in e.flags]
        if not available:
            raise ReviewError('No complete translations are available for this simulation. Use normal synchronization for this language.')
        selected = []
        for predicate in [lambda e: bool(e.msgid_plural), lambda e: '%{' in e.msgid or '%s' in e.msgid, lambda e: len(e.msgid.split()) >= 3]:
            match = next(((k,e) for k,e in available if k not in {x[0] for x in selected} and predicate(e)),None)
            if match:
                selected.append(match)
        selected += [pair for pair in available if pair[0] not in {x[0] for x in selected}][:3-len(selected)]
        replacements = {k:['']*len(forms(e)) for k,e in selected}
        payload['simulation'] = True
        payload['simulation_reference'] = {k:forms(e) for k,e in selected}
        payload['remote_target'] = payload['target']
        payload['target'] = target.render(replacements)
        if payload.get('native_yaml') is not None:
            payload['remote_yaml'] = payload['native_yaml']
            parser,data,lang=yaml_load(payload['native_yaml'],self.language,payload.get('metadata',{}).get(self.language,{}).get('language',{}).get('aliases',[]))
            for k,e in selected:
                item=payload['yaml_mapping'][k]
                paths=[item['path']+[name] for name in item['plural_forms']] if item['plural_forms'] else [item['path']]
                for path in paths:
                    assign(data[lang],path,'')
            stream=io.StringIO();parser.dump(data,stream);payload['native_yaml']=stream.getvalue()
        if payload.get('native_json') is not None:
            from payload import json_load
            payload['remote_json'] = payload['native_json']
            data = json_load(payload['native_json'])
            for k, e in selected:
                data[payload['json_mapping'][k]]['message'] = ''
            payload['native_json'] = json.dumps(data, ensure_ascii=False, indent=4) + '\n'
        payload.setdefault('warnings',[]).append(f'SIMULATION: {len(selected)} entries were cleared in this local copy only. Do not upload its exports to Weblate.')
        return payload
