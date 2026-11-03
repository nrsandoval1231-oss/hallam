"""Browser-local, candidate-scoped opportunity saves; never changes pursuit state."""

SAVED_JS = r"""
function artemisSaveButton(id,company){return '<button type="button" class="save-opportunity" data-save="'+_vesc(id)+'" aria-pressed="false" aria-label="Save opportunity at '+_vesc(company||'this company')+'" title="Save in this browser"><span class="save-icon" aria-hidden="true"></span></button>';}
function artemisSyncSaves(){
  if(typeof document.querySelectorAll!=='function')return;
  document.querySelectorAll('[data-save]').forEach(b=>{const saved=(globalThis.__artemisSaved||[]).includes(b.dataset.save);b.setAttribute('aria-pressed',String(saved));b.title=saved?'Saved in this browser; select to remove':'Save in this browser';b.disabled=!globalThis.__artemisSaveKey;});
}
function artemisInitSaves(candidate){
  globalThis.__artemisSaveKey=candidate?'artemis:saved:v1:'+candidate:null;
  try{const values=JSON.parse(localStorage.getItem(globalThis.__artemisSaveKey)||'[]');globalThis.__artemisSaved=Array.isArray(values)?values.filter(x=>typeof x==='string'):[];}catch(e){globalThis.__artemisSaved=[];}
  artemisSyncSaves();
}
if(typeof document.addEventListener==='function'&&!globalThis.__artemisSaveWired){
  globalThis.__artemisSaveWired=true;
  document.addEventListener('click',event=>{
    const button=event.target&&event.target.closest&&event.target.closest('[data-save]');
    if(!button||!globalThis.__artemisSaveKey)return;
    event.preventDefault();
    const values=new Set(globalThis.__artemisSaved||[]),id=button.dataset.save;
    if(values.has(id))values.delete(id);else values.add(id);
    try{localStorage.setItem(globalThis.__artemisSaveKey,JSON.stringify([...values]));globalThis.__artemisSaved=[...values];artemisSyncSaves();}
    catch(e){button.title='Browser storage is unavailable. Opportunity could not be saved.';button.setAttribute('aria-label',button.title);}
  });
  if(typeof window!=='undefined')window.addEventListener('storage',event=>{if(event.key===globalThis.__artemisSaveKey)artemisInitSaves(globalThis.__artemisSaveKey.slice('artemis:saved:v1:'.length));});
}
"""
