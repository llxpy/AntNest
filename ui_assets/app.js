function applyBg(i){
  if(i){
    document.body.style.backgroundImage='linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url("'+i+'")';
    document.body.style.backgroundSize="cover";
    document.body.style.backgroundPosition="center";
    document.body.style.backgroundAttachment="fixed";
  }else{
    document.body.style.backgroundImage="";
    document.body.style.backgroundSize="";
    document.body.style.backgroundPosition="";
    document.body.style.backgroundAttachment="";
  }
}
function setStopEnabled(on){
  var b=document.getElementById("btn-stop");
  if(b) b.disabled=!on;
}
function scrollChat(){
  var b=document.querySelector("#chat-msgs");
  if(b) b.scrollTop=b.scrollHeight;
}
var streamBubbleEl=null;
var QUEEN_AVATAR_HTML='<span class="avatar queen-avatar">'
  +'<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
  +'<circle cx="12" cy="5.5" r="2.6"/><circle cx="12" cy="11" r="2"/>'
  +'<ellipse cx="12" cy="18" rx="3.6" ry="4.4"/>'
  +'<path d="M7.2 9.2l3.2 1.6M16.8 9.2l-3.2 1.6M7 14.5l3.2-1M17 14.5l-3.2-1M7.8 19.5l3-1M16.2 19.5l-3-1" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>'
  +'</svg></span>';
function startStreamBubble(){
  var old=document.getElementById("stream-bubble");
  if(old) old.remove();
  var box=document.getElementById("chat-msgs");
  if(!box) return;
  streamBubbleEl=document.createElement("div");
  streamBubbleEl.className="bubble queen streaming";
  streamBubbleEl.id="stream-bubble";
  streamBubbleEl.innerHTML=QUEEN_AVATAR_HTML
    +'<div class="bubble-main">'
    +'<div class="think-block" id="stream-think-wrap" style="display:none">'
    +'<div class="think-title">深度思考</div><div class="think-body" id="stream-think"></div></div>'
    +'<div class="bubble-text" id="stream-text"></div>'
    +'</div>';
  box.appendChild(streamBubbleEl);
  scrollChat();
}
function ensureStreamThink(){
  var w=document.getElementById("stream-think-wrap");
  if(w){ w.style.display=""; }
}
function appendStreamThink(t){
  ensureStreamThink();
  var el=document.getElementById("stream-think");
  if(el) el.textContent+=t;
  scrollChat();
}
function closeStreamThink(){
  var w=document.getElementById("stream-think-wrap");
  if(w) w.open=false;
}
function appendStreamText(t){
  var el=document.getElementById("stream-text");
  if(el) el.textContent+=t;
  scrollChat();
}
function finalizeStreamBubble(){
  streamBubbleEl=null;
  scrollChat();
}
function closeAllModals(){
  closeSettings();
  closeSkillsModal();
  closeSponsorModal();
}
function phwCall(route, payload){
  console.log("[phwCall]", route, payload);
  if(window.PHW && window.PHW.route){ window.PHW.route(route, payload||{}); return; }
  console.warn("[phwCall] window.PHW 未就绪，回退 fetch（webview 模式下会失败）");
  fetch("/api/route",{method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({route:route,data:payload||{}})});
}
var selectedSkill="";
var selectedImage=null; // {b64, mime}
function onSkillChange(v){
  selectedSkill=v;
  phwCall("skill_change",{value:v});
}
function setSkillSelect(v){
  selectedSkill=v;
  var s=document.getElementById("skill-select");
  if(s) s.value=v;
}
function onImageFile(input){
  if(!canAttachImage()) return;
  var f=input.files && input.files[0];
  if(!f) return;
  attachImage(f);
  input.value="";
}
function canAttachImage(){
  var b=document.getElementById("image-upload-btn");
  return !!b && !b.disabled;
}
function updateImageBtn(enabled, reason){
  var b=document.getElementById("image-upload-btn");
  if(!b) return;
  b.disabled=!enabled;
  b.title = enabled ? "上传图片" : (reason || "当前模型不支持图片识别");
  if(!enabled && selectedImage){ removeImage(); }
}
function attachImage(file){
  if(!file.type.startsWith("image/")){
    alert("请选择图片文件"); return;
  }
  var reader=new FileReader();
  reader.onload=function(e){
    var data=e.target.result; // data:image/png;base64,....
    var idx=data.indexOf(",");
    var mime=data.slice(5, idx);
    var b64=data.slice(idx+1);
    selectedImage={b64:b64, mime:mime};
    phwCall("image_attach",{b64:b64, mime:mime});
    renderImagePreview();
  };
  reader.readAsDataURL(file);
}
function renderImagePreview(){
  var row=document.getElementById("image-preview-row");
  if(!row) return;
  if(!selectedImage){ row.innerHTML=""; return; }
  row.innerHTML='<div class="image-preview"><img src="data:'+selectedImage.mime+';base64,'+selectedImage.b64+'"><button class="rm" onclick="removeImage()">×</button></div>';
}
function removeImage(){
  selectedImage=null;
  phwCall("image_attach",{b64:"", mime:""});
  renderImagePreview();
}
function clearInputAndImage(){
  var i=document.getElementById("chat-input");
  if(i) i.value="";
  selectedImage=null;
  renderImagePreview();
}
function onChatPaste(e){
  var cd=(e.clipboardData||e.originalEvent.clipboardData);
  if(!cd) return;
  var hasImage=false;
  if(cd.items && cd.items.length){
    for(var k=0;k<cd.items.length;k++){
      if(cd.items[k].type.indexOf("image")!==-1){ hasImage=true; break; }
    }
  }
  if(!hasImage && cd.files && cd.files.length){
    for(var k=0;k<cd.files.length;k++){
      if(cd.files[k].type.indexOf("image")!==-1){ hasImage=true; break; }
    }
  }
  if(!hasImage) return;
  if(!canAttachImage()){
    var reason=window.VISION_REASON||"当前模型不支持图片或未校验 API";
    alert("无法粘贴图片："+reason+"\n请在设置中配置 vision 模型并点「保存并校验」。");
    e.preventDefault();
    return;
  }
  var handled=false;
  if(cd.items && cd.items.length){
    for(var k=0;k<cd.items.length;k++){
      if(cd.items[k].type.indexOf("image")!==-1){
        e.preventDefault();
        attachImage(cd.items[k].getAsFile());
        handled=true; break;
      }
    }
  }
  if(!handled && cd.files && cd.files.length){
    for(var k=0;k<cd.files.length;k++){
      if(cd.files[k].type.indexOf("image")!==-1){
        e.preventDefault();
        attachImage(cd.files[k]);
        handled=true; break;
      }
    }
  }
}
document.addEventListener("paste", function(e){
  var t=e.target;
  if(!t) return;
  if(t.id==="chat-input" || (t.closest && (t.closest(".chat-col")||t.closest(".chat-input-row"))))
    onChatPaste(e);
});
var _updateUrl="";
function showUpdateBanner(tag, url, cur){
  _updateUrl=url||"";
  var b=document.getElementById("update-banner");
  if(!b) return;
  var t=document.getElementById("update-text");
  if(t) t.textContent="发现新版本 v"+tag+"（当前 v"+cur+"）";
  b.style.display="flex";
}
function hideUpdateBanner(){
  var b=document.getElementById("update-banner");
  if(b) b.style.display="none";
}
function openRelease(){
  if(_updateUrl) phwCall("open_release",{url:_updateUrl});
}
function checkForUpdate(){
  var btn=document.getElementById("btn-check-update");
  if(btn){btn.textContent="检查中..."; btn.disabled=true;}
  phwCall("check_update",{});
}
function onSend(){
  var i=document.getElementById("chat-input");
  if(!i){ console.warn("[onSend] 找不到 #chat-input"); return; }
  var v=(i.value||"").trim();
  console.log("[onSend] value=", v, " skill=", selectedSkill, " image=", !!selectedImage);
  if(!v && !selectedImage){ console.log("[onSend] 空消息，忽略"); return; }
  i.value="";
  var payload={value:v, skill:selectedSkill};
  if(selectedImage && canAttachImage()){ payload.image_b64=selectedImage.b64; payload.image_mime=selectedImage.mime; }
  selectedImage=null;
  renderImagePreview();
  phwCall("send",payload);
}
document.addEventListener("keydown",function(e){
  if(e.key==="Escape"){
    closeAllModals();
    return;
  }
  // 输入法合成中（拼音确认候选）不要拦截 Enter，交给 IME 上屏；否则会发出半截拼音
  if(e.isComposing || e.keyCode === 229) return;
  if(e.key==="Enter" && !e.shiftKey && document.activeElement &&
     document.activeElement.id==="chat-input"){ e.preventDefault(); onSend(); }
});
// 针对「输入法检测不到输入框」：pywebview/WebView2 的 WinForms 宿主有时不把焦点交给
// WebView2 控件，导致 IME 无法在该 input 上激活（inline 拼音都不出现）。这里在 webview 就绪后、
// 以及点击聊天输入区时，强制把焦点塞进输入框，逼 IME 接管。
function focusChat(){
  var i=document.getElementById("chat-input");
  if(i){ try{ i.focus({preventScroll:true}); }catch(_){ i.focus(); } }
}
if(window.pywebview && window.pywebview.api){ focusChat(); }
window.addEventListener("pywebviewready", function(){ focusChat(); });
document.addEventListener("click", function(e){
  var t=e.target;
  if(t && (t.tagName==="BUTTON" || t.closest("button") || t.closest(".input-actions"))) return;
  if(t && (t.tagName==="SELECT" || (t.closest && t.closest(".skill-select")))) return;
  // 正在选字/拖选文本时不抢焦点
  var sel=window.getSelection();
  if(sel && !sel.isCollapsed && String(sel).trim()) return;
  if(t && (t.id==="chat-input" ||
          (t.closest && t.closest(".chat-input-wrap")))){ focusChat(); }
});
function toggleSettings(){var m=document.querySelector("#settings-modal"); if(m)m.classList.toggle("show");}
function closeSettings(){var m=document.querySelector("#settings-modal"); if(m)m.classList.remove("show");}
/* ===== 对话记录 ===== */
function openSessionsModal(){
  var m=document.querySelector("#sessions-modal"); if(!m) return;
  m.classList.add("show");
  refreshSessions();
}
function closeSessionsModal(){var m=document.querySelector("#sessions-modal"); if(m)m.classList.remove("show");}
function refreshSessions(){ phwCall("sessions_list",{}); }
function setSessionsHint(t){var h=document.getElementById("sessions-hint"); if(h)h.textContent=t||"";}
function fmtTime(ts){
  try{
    var d=new Date(ts*1000), p=function(n){return (n<10?"0":"")+n;};
    return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes());
  }catch(e){ return ""; }
}
function renderSessions(list){
  var box=document.getElementById("sessions-list"); if(!box) return;
  setSessionsHint("");
  if(!list || !list.length){ box.innerHTML='<div class="muted" style="padding:20px 4px;text-align:center">还没有历史会话，发送第一条消息后会自动保存。</div>'; return; }
  box.innerHTML=list.map(function(s){
    var id=escHtml(s.id), time=fmtTime(s.time), prev=escHtml(s.preview||"");
    return '<div class="session-item">'
      +'<div class="session-info" onclick="loadSession(\''+id+'\')" title="点击恢复该会话">'
      +'<div class="session-preview">'+prev+'</div>'
      +'<div class="session-meta">'+time+' · '+s.count+' 条消息 · '+s.size_kb+' KB</div>'
      +'</div>'
      +'<button class="session-del" onclick="deleteSession(\''+id+'\')" title="删除该会话">×</button>'
      +'</div>';
  }).join("");
}
function loadSession(id){ if(!id) return; phwCall("session_load",{id:id}); }
function deleteSession(id){
  if(!id) return;
  if(!confirm("确定删除这个会话吗？删除后不可恢复。")) return;
  phwCall("session_delete",{id:id});
}
function newSession(){ phwCall("session_new",{}); }
/* ===== 对话记录 END ===== */
function openSkillsModal(){
  var m=document.querySelector("#skills-modal"); if(m)m.classList.add("show");
  refreshSkills();
}
function closeSkillsModal(){var m=document.querySelector("#skills-modal"); if(m)m.classList.remove("show");}
function toggleSkillsModal(){var m=document.querySelector("#skills-modal"); if(m)m.classList.toggle("show");}
function escHtml(s){
  return (s||"").replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});
}
function openSponsorModal(images){
  var m=document.getElementById("sponsor-modal");
  var g=document.getElementById("sponsor-images");
  if(!m || !g) return;
  g.innerHTML=(images||[]).map(function(x){
    return '<div class="qr-item"><img src="data:'+escHtml(x.mime)+';base64,'+escHtml(x.b64)+'" alt="'+escHtml(x.name)+'">'
         +'<span>'+escHtml(x.name)+'</span></div>';
  }).join("");
  m.classList.add("show");
}
function closeSponsorModal(){var m=document.getElementById("sponsor-modal"); if(m)m.classList.remove("show");}
function refreshSkills(){ phwCall("refresh_skills",{}); }
function setSkillsDir(p){
  var el=document.getElementById("set-skills_dir");
  if(el) el.value=p;
}
function onBrowseSkillsDir(){
  phwCall("browse_skills_dir",{});
}
function setTheme(key){
  document.documentElement.setAttribute("data-theme", key);
  document.querySelectorAll(".theme-btn").forEach(function(b){b.classList.remove("active");});
  var a=document.querySelector('.theme-btn[value="'+key+'"]');
  if(a)a.classList.add("active");
  var h=document.getElementById("set-theme");
  if(h)h.value=key;
  var bg=document.getElementById("set-bg_image");
  applyBg(bg?bg.value:"");
}
// 页面就绪后主动刷新 skills 列表：必须等 PHW 桥接就绪，否则 webview 模式下
// window.PHW 尚未注入，会走失败的 fetch 回退导致刷新静默丢失。
(function whenReady(){
  if(window.PHW && window.PHW.route){ refreshSkills(); return; }
  var tries=0;
  var check=function(){
    if(window.PHW && window.PHW.route){ refreshSkills(); return; }
    if(++tries>=80){ console.warn("[refreshSkills] PHW 超时未就绪"); return; }
    setTimeout(check, 50);
  };
  if(window.addEventListener){ window.addEventListener("pywebviewready", check); }
  setTimeout(check, 50);
})();

function syncToggle(key){
  var cb=document.getElementById("set-"+key+"-cb");
  var hid=document.getElementById("set-"+key);
  if(cb&&hid) hid.value=cb.checked?"true":"false";
}
function syncAllToggles(){
  ["mcp_enabled","skills_enabled","skip_model_check"].forEach(syncToggle);
}
function applyModelRecommendations(patch){
  if(!patch) return;
  if(patch.thinking_mode){
    var t=document.getElementById("set-thinking_mode");
    if(t) t.value=patch.thinking_mode;
  }
  if(patch.skip_model_check){
    var hid=document.getElementById("set-skip_model_check");
    var cb=document.getElementById("set-skip_model_check-cb");
    var on=String(patch.skip_model_check).toLowerCase()==="true";
    if(hid) hid.value=on?"true":"false";
    if(cb) cb.checked=on;
  }
}
function collectSettings(){
  syncAllToggles();
  var settings={};
  ["llm_base_url","llm_model","llm_api_key","thinking_mode","skip_model_check","max_depth","max_clones",
   "mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)settings[k]=el.value;
  });
  return settings;
}
function refreshSkillOptions(list){
  var s=document.getElementById("skill-select");
  if(!s) return;
  var old=s.value;
  s.innerHTML='<option value="">不使用 Skill</option>'+
    (list||[]).map(function(x){return '<option value="'+x.name+'">'+x.name+'</option>';}).join("");
  s.value=old;
}
function setVerifyHint(text, cls){
  var h=document.getElementById("verify-hint");
  if(h){h.textContent=text||""; h.className="verify-hint "+(cls||"");}
}
function onListModels(){
  setVerifyHint("正在检测模型列表…","");
  phwCall("list_models",{settings:collectSettings()});
}
function openModelsModal(models){
  var m=document.getElementById("models-modal");
  if(m) m.classList.add("show");
}
function closeModelsModal(){
  var m=document.getElementById("models-modal");
  if(m) m.classList.remove("show");
}
function pickModel(id){
  var el=document.getElementById("set-llm_model");
  if(el) el.value=id;
  phwCall("pick_model",{value:id});
  closeModelsModal();
}
function onTestApi(){
  setVerifyHint("正在校验…","");
  phwCall("test_api",{settings:collectSettings()});
}
function onSaveSettings(){
  syncAllToggles();
  var settings={};
  var appearance={};
  ["llm_base_url","llm_model","llm_api_key","thinking_mode","skip_model_check","max_depth","max_clones","mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)settings[k]=el.value;
  });
  ["bg_image","custom_agent"].forEach(function(k){
    var el=document.getElementById("set-"+k);
    if(el)appearance[k]=el.value;
  });
  var th=document.getElementById("set-theme");
  if(th)appearance.theme=th.value;
  phwCall("save_settings",{settings:settings,appearance:appearance});
  closeSettings();
}