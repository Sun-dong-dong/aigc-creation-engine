import importlib.util,json,copy,tempfile
from pathlib import Path
p=Path(__file__).with_name('validate_task.py')
spec=importlib.util.spec_from_file_location('gate',p);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
with tempfile.TemporaryDirectory(prefix='aigc-fixed-') as temp:
 root=Path(temp);file=root/'fixed.json'
 fixed={'project_id':'test-project','version':'v1','confirmed':True,'confirmation_evidence':'Test user approval','styles':{k:'Natural photographic texture.' for k in ['h3-base','h3-ref','seedance-2.0','seedance-2.5']},'scenes':[{'id':'room-day','applies_to':'Room in daylight','confirmed':True,'confirmation_evidence':'Test scene approval','texts':{k:'Soft daylight enters through the window.' for k in ['h3-base','h3-ref','seedance-2.0','seedance-2.5']}}]}
 file.write_text(json.dumps(fixed));bind={'path':'fixed.json','sha256':g.sha(file.read_bytes()),'project_id':'test-project','version':'v1','scene_ids':['room-day'],'reviewed':True,'review_evidence':'Checked scope and conflicts'}
 task={'profile':'h3-ref','fixed_context':bind}
 prompt='detailed_description:\nNatural photographic texture. Soft daylight enters through the window.\n[Shot 1] The man sits.\noverall_soundscape:\nRoom tone.'
 def check(t=task,s=prompt):
  e=[];g.check_fixed_context(root,t,s,e);return e
 assert not check(),check()
 assert check(s=prompt.replace('Natural photographic texture.','Realistic texture.'))
 assert check(s=prompt.replace('Natural photographic texture.',''))
 assert check(s=prompt.replace('Soft daylight enters through the window.',''))
 assert check(s=prompt.replace('Natural photographic texture.','<d>[English] Natural photographic texture.</d>'))
 assert check(s=prompt.replace('Natural photographic texture.','"Natural photographic texture."'))
 t=copy.deepcopy(task);t['fixed_context']['version']='v0';assert check(t)
 t=copy.deepcopy(task);t['fixed_context']['project_id']='other';assert check(t)
 t=copy.deepcopy(task);t['fixed_context']['scene_ids']=['unknown'];assert check(t)
 assert check(t={'profile':'h3-ref'})
 for profile,field in [('h3-base','integrated_multimodal_description'),('seedance-2.0',None),('seedance-2.5',None)]:
  t=copy.deepcopy(task);t['profile']=profile;s=prompt.replace('detailed_description',field) if field else 'Natural photographic texture. Soft daylight enters through the window.\n镜头1：男子坐着。'
  assert not check(t,s),check(t,s)
 # Regression cases from independent review: embedded quotes, alternate quotes, typed evidence.
 original = copy.deepcopy(fixed)
 fixed['styles']['h3-ref']='Use a "soft pastel" palette.'
 file.write_text(json.dumps(fixed));task['fixed_context']['sha256']=g.sha(file.read_bytes())
 assert not check(s=prompt.replace('Natural photographic texture.',fixed['styles']['h3-ref']))
 fixed=copy.deepcopy(original);file.write_text(json.dumps(fixed));task['fixed_context']['sha256']=g.sha(file.read_bytes())
 for left,right in [('「','」'),('『','』')]:
  t=copy.deepcopy(task);t['profile']='seedance-2.0'
  assert check(t,left+'Natural photographic texture.'+right+' Soft daylight enters through the window.')
 t=copy.deepcopy(task);t['fixed_context']['review_evidence']=['Invalid evidence'];assert check(t)
 t=copy.deepcopy(task);t['fixed_context']['scene_ids']=[];t['fixed_context']['scene_omission_reason']=12;assert check(t)
 fixed['confirmation_evidence']=True;file.write_text(json.dumps(fixed));task['fixed_context']['sha256']=g.sha(file.read_bytes());assert check()
 fixed=original;file.write_text(json.dumps(fixed));task['fixed_context']['sha256']=g.sha(file.read_bytes())
 # The actual file content, not just task metadata, must invalidate a previous input fingerprint.
 task_for_hash={'profile':'h3-ref','prompt_file':'prompt.txt','fixed_context':bind}
 (root/'prompt.txt').write_text(prompt);(root/'task.json').write_text(json.dumps(task_for_hash));before=g.fingerprint(root/'task.json')
 fixed['styles']['h3-ref']='Different approved style.';file.write_text(json.dumps(fixed));assert before != g.fingerprint(root/'task.json');assert check()
 fixed['confirmed']=False;file.write_text(json.dumps(fixed));assert any('确认' in e for e in check())
 print('PASS: approved exact text across four profiles; omitted/reworded text, quoted/dialogue-only text, wrong project/version/scene, missing binding, stale file, unconfirmed style rejected; input fingerprint invalidated by actual file changes.')
