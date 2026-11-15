"""Offline regression cases for per-shot lighting evidence; no model calls."""
import copy
import importlib.util
from pathlib import Path
p=Path(__file__).with_name('validate_task.py')
spec=importlib.util.spec_from_file_location('g5',p);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
light='A soft 5600K window light from the east wall illuminates the left side of the man\'s face.'
row={'temperature_quote':'5600K window light','source_quote':'window light from the east wall','characters_present':True,'characters':[{'subject':'man','prompt_quote':light}],'basis':'Confirmed test room daylight design and one visible man.'}
task={'shots':[{'id':'01'}],'review':{'lighting':{'01':row}}};blocks={'01':light}
def check(t=task,b=blocks):
 e=[];g.check_shot_lighting(t,b,e);return e
assert not check(),check()
t=copy.deepcopy(task);t['review']['lighting']={};assert check(t)
for field in ['temperature_quote','source_quote']:
 t=copy.deepcopy(task);t['review']['lighting']['01'][field]='';assert check(t)
t=copy.deepcopy(task);t['review']['lighting']['01']['temperature_quote']='soft';assert check(t)
t=copy.deepcopy(task);t['review']['lighting']['01']['characters']=[];assert check(t)
t=copy.deepcopy(task);t['review']['lighting']['01']['characters'][0]['prompt_quote']='The wall is lit.';assert check(t)
assert check(b={'01':'<d>[English] '+light+'</d>'})
assert check(b={'01':'No lighting specified.','02':light})
t=copy.deepcopy(task);t['review']['lighting']['01'].update(characters_present=False,characters=[],basis='Confirmed empty room shot.');assert not check(t)
t=copy.deepcopy(task);t['shots'].append({'id':'02'});assert check(t)
for phrase in ['窗外日光5600K', '5600K照亮室内']:
 t=copy.deepcopy(task);r=t['review']['lighting']['01'];r['temperature_quote']=phrase;r['source_quote']=phrase;r['characters'][0]['prompt_quote']=phrase
 assert not check(t,{'01':phrase})
print('PASS: visible-character and empty-scene lighting; missing source/K/character evidence, dialogue-only evidence, wrong-shot evidence and incomplete shot coverage rejected.')
