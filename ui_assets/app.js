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



var _chatUserNearBottom=true;
function scrollChat(){
  var b=document.querySelector("#chat-msgs");
  if(b&&_chatUserNearBottom) b.scrollTop=b.scrollHeight;
}




// ===== 拖放：文件拖入聊天 =====







var _dragDepth=0;







function initChatDragDrop(){



  var col=document.querySelector(".chat-col");



  if(!col) return;



  window.addEventListener("dragover", function(e){ e.preventDefault(); }, false);



  window.addEventListener("drop", function(e){ e.preventDefault(); }, false);



  col.addEventListener("dragenter", function(e){



    var dt=e.dataTransfer; if(!dt || !dt.types) return;



    var has=false;



    for(var k=0;k<dt.types.length;k++){ if(dt.types[k]==="Files"){ has=true; break; } }



    if(!has) return;



    e.preventDefault(); _dragDepth++;



    col.classList.add("drag-over");



  });



  col.addEventListener("dragleave", function(){



    _dragDepth=Math.max(0,_dragDepth-1);



    if(!_dragDepth) col.classList.remove("drag-over");



  });



  col.addEventListener("drop", function(e){



    e.preventDefault(); _dragDepth=0;



    col.classList.remove("drag-over");



    var fs=e.dataTransfer && e.dataTransfer.files;



    if(!fs || !fs.length) return;



    var ta=document.getElementById("chat-input");



    var attached=false, names=[];



    for(var k=0;k<fs.length;k++){



      var f=fs[k];



      if(f.type && f.type.indexOf("image/")===0){



        if(!attached && canAttachImage()){ attachImage(f); attached=true; }



        else names.push(f.name);



      } else { names.push(f.name); }



    }



    if(names.length && ta){



      ta.value += (ta.value ? "\n" : "") + names.map(function(n){ return "[文件] " + n; }).join("\n");



      phwCall("chat_input",{value:ta.value});



      autoGrowInput(ta);



      ta.focus();



    }



  });



}



(function(){
  var b=document.querySelector("#chat-msgs");
  if(!b)return;
  b.addEventListener("scroll",function(){
    _chatUserNearBottom=(b.scrollHeight-b.scrollTop-b.clientHeight)<100;
  });
})();



var streamBubbleEl=null;



var QUEEN_AVATAR_HTML='<span class="avatar queen-avatar">'



  +'<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="none">'



  +'<path d="M12 2.4 L20.3 7.2 V16.8 L12 21.6 L3.7 16.8 V7.2 Z" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.45"/>'



  +'<ellipse cx="12" cy="17" rx="3.5" ry="4.2" fill="currentColor"/>'



  +'<ellipse cx="10.6" cy="15.9" rx="1.5" ry="2.1" fill="#ffffff" fill-opacity="0.18"/>'



  +'<circle cx="12" cy="11.2" r="1.9" fill="currentColor"/>'



  +'<circle cx="12" cy="6.9" r="2.4" fill="currentColor"/>'



  +'<path d="M10.5 5.2 C9.3 3.5 7.9 2.9 6.6 3 M13.5 5.2 C14.7 3.5 16.1 2.9 17.4 3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>'



  +'<path d="M7.5 9.3 L5.5 7.5 M7.7 11.6 L5.1 10.3 M7.4 14.1 L4.8 14.8 M16.5 9.3 L18.5 7.5 M16.3 11.6 L18.9 10.3 M16.6 14.1 L19.2 14.8" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>'



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



    +'<div class="bubble-meta"><span class="bm-dot"></span><span class="bm-label">蚁后 · Queen</span></div>'



    +'<div class="think-block" id="stream-think-wrap">'



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



function preserveInterruptedBubble(reason){



  var bubble=document.getElementById("stream-bubble");



  if(!bubble) return;



  bubble.classList.add("interrupt-note");



  var title=bubble.querySelector(".think-title");



  if(title) title.textContent="已保存的中断摘要";



  var text=bubble.querySelector("#stream-text");



  if(text && !text.textContent.trim()) text.textContent="本轮在用户主动停止后暂停，下一轮会先理解停止原因。";



  if(reason){



    var body=bubble.querySelector("#stream-think");



    if(body && !body.textContent.trim()) body.textContent=reason;



    ensureStreamThink();



  }



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
// 自绘标题栏窗口控制（frameless）：最小化/最大化/关闭。
// 浏览器模式无此能力，静默返回。
function winCtrl(action){
  if(window.pywebview && window.pywebview.api && window.pywebview.api.win){
    window.pywebview.api.win(action);
  }else{
    console.log("[winCtrl]", action, "(browser/legacy mode, no-op)");
  }
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



  if(btn){btn.querySelector(".btn-lbl").textContent="检查中..."; btn.disabled=true;}



  phwCall("check_update",{});



}



function onSend(){



  var i=document.getElementById("chat-input");



  if(!i){ console.warn("[onSend] 找不到 #chat-input"); return; }



  var v=(i.value||"").trim();



  console.log("[onSend] value=", v, " skill=", selectedSkill, " image=", !!selectedImage);



  if(!v && !selectedImage){ console.log("[onSend] 空消息，忽略"); return; }



  i.value="";



  autoGrowInput(i);  // 发送后恢复单行高
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



  if(!list || !list.length){ box.innerHTML='<div class="muted" style="grid-column:1/-1;padding:24px;text-align:center">还没有历史会话，发送第一条消息后会自动保存。</div>'; return;}



  box.innerHTML=list.map(function(s){



    var id=escHtml(s.id), time=fmtTime(s.time), prev=escHtml(s.preview||"");



    return '<div class="session-card">'



      +'<div class="session-card-head">'



      +'<span class="session-time">'+time+'</span>'



      +'<span class="session-count">'+s.count+' 条</span>'



      +'</div>'



      +'<div class="session-preview">'+prev+'</div>'



      +'<div class="session-meta">'+s.size_kb+' KB · '+s.count+' 条消息</div>'



      +'<div class="session-actions">'



      +'<button class="btn" onclick="openSessionView(\''+id+'\')" title="只读查看该会话内容">查看</button>'



      +'<button class="btn ghost" onclick="loadSession(\''+id+'\')" title="将该会话恢复为当前上下文">恢复上下文</button>'



      +'<button class="btn ghost session-del" onclick="deleteSession(\''+id+'\')" title="删除该会话">×</button>'



      +'</div>'



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



  var row=document.getElementById("custom-color-row");



  if(row) row.style.display=(key==="custom")?"flex":"none";



  if(key==="custom"){



    var c=document.getElementById("set-custom-color");



    if(c) applyCustomColor(c.value||"#4CC9F0");



  }



  var bg=document.getElementById("set-bg_image");



  applyBg(bg?bg.value:"");



}



function applyCustomColor(v){



  var ds=document.documentElement.style;



  ds.setProperty("--user-accent", v||"#4CC9F0");



  document.documentElement.setAttribute("data-theme","custom");



  var h=document.getElementById("set-theme"); if(h)h.value="custom";



  var c=document.getElementById("set-custom-color"); if(c)c.value=v||"#4CC9F0";



  document.querySelectorAll(".theme-btn").forEach(function(b){b.classList.remove("active");});



  var a=document.querySelector('.theme-btn[value="custom"]'); if(a)a.classList.add("active");



  var row=document.getElementById("custom-color-row"); if(row)row.style.display="flex";



  phwCall("custom_color",{value:v||"#4CC9F0"});



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



   "temperature","compact_threshold","worker_timeout","mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){



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



  ["llm_base_url","llm_model","llm_api_key","thinking_mode","skip_model_check","max_depth","max_clones","temperature","compact_threshold","worker_timeout","mcp_enabled","mcp_config","skills_enabled","skills_dir"].forEach(function(k){



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



// 子任务 / 工蚁 展开模态（卡片式排列，显示执行状态）



function openSubtasksModal(){



  var m=document.getElementById("subtasks-modal"); if(m)m.classList.add("show");



}



function closeSubtasksModal(){



  var m=document.getElementById("subtasks-modal"); if(m)m.classList.remove("show");



}



function openWorkersModal(){



  var m=document.getElementById("workers-modal"); if(m)m.classList.add("show");



}



// ==== v1.4：事件轨迹 ====








function openTimelineModal(){





  var m=document.getElementById("timeline-modal"); if(!m) return;





  m.classList.add("show");





  var box=document.getElementById("timeline-modal-list");





  if(box) box.innerHTML='<div class="muted">加载中</div>';





  phwCall("replay_task",{}).then(function(r){





    if(!r) return;





    var box=document.getElementById("timeline-modal-list"); if(!box) return;





    var t=String(r.text||"");





    if(!t || t.indexOf("没有事件")>=0 || t.indexOf("重放不可用")>=0){





      box.innerHTML='<div class="muted">本任务暂无事件。</div>'; return;





    }





    box.innerHTML="";





    t.split(String.fromCharCode(10)).forEach(function(line){





      if(!line) return;





      var parts=line.split(/  +/);





      var time=parts[0]||"";





      var name=parts[1]||"";





      var rest=parts.slice(2).join("  ");





      var row=document.createElement("div"); row.className="tl-item";





      [["tl-time",time],["tl-event",name],["tl-detail",rest]].forEach(function(pair){





        var sp=document.createElement("span"); sp.className=pair[0];





        sp.textContent=pair[1]; row.appendChild(sp);





      });





      box.appendChild(row);





    });





  });





}





function closeTimelineModal(){





  var m=document.getElementById("timeline-modal"); if(m) m.classList.remove("show");





}





function closeWorkersModal(){



  var m=document.getElementById("workers-modal"); if(m)m.classList.remove("show");



}



// ==== v1.3.0：会话只读查看（查看而非恢复上下文） ====



function openSessionView(id){ if(!id) return; phwCall("session_view",{id:id}); }



function openSessionViewModal(){ var m=document.getElementById("session-view-modal"); if(m)m.classList.add("show"); }



function closeSessionViewModal(){ var m=document.getElementById("session-view-modal"); if(m)m.classList.remove("show"); }



function renderSessionView(msgs){



  var box=document.getElementById("session-view-body"); if(!box) return;



  if(!msgs || !msgs.length){ box.innerHTML='<div class="muted" style="padding:20px;text-align:center">该会话暂无消息</div>'; return; }



  box.innerHTML=msgs.map(function(m){



    var cls = m.role==="user" ? "sv-item sv-user" : "sv-item sv-queen";



    var av  = m.role==="user" ? "你" : "蚁后";



    var body = '<div class="sv-text">'+(escHtml(m.text||""))+'</div>';



    if(m.reasoning){



      body = '<div class="think-block"><div class="think-title">深度思考</div>'



           + '<div class="think-body">'+escHtml(m.reasoning)+'</div></div>' + body;



    }



    return '<div class="'+cls+'"><span class="sv-avatar">'+av+'</span><div class="sv-body">'+body+'</div></div>';



  }).join("");



}



// ===== 右下角迷你常驻窗（关窗即退出模式，此段为兼容旧交互保留；关闭按钮即真退出）=====


function showMiniPanel(){


  var m=document.getElementById("mini-panel");


  if(m) m.style.display="block";


  var s=document.getElementById("mini-state");


  if(s) s.textContent=(window.PHW && window.PHW.busy) ? "执行中…" : "待命";


  var i=document.getElementById("mini-input");


  if(i) i.focus();


}


function hideMiniPanel(){


  var m=document.getElementById("mini-panel");


  if(m) m.style.display="none";


}


function restoreWindow(){


  hideMiniPanel();


  phwCall("restore_window",{});


}


function miniSend(){


  var i=document.getElementById("mini-input");


  if(!i || !i.value.trim()) return;


  var v=i.value.trim(); i.value="";


  phwCall("mini_send",{value:v});


}


function quitApp(){ phwCall("quit",{}); }





// ===== 模型快速切换（对话框内下拉）=====


function onModelChange(v){


  if(!v || v==="") return;


  phwCall("pick_model",{value:v});


}


function setModelSelect(v){



  var s=document.getElementById("model-select");



  if(!s) return;



  v=v||"";



  var has=false;



  for(var k=0;k<s.options.length;k++){ if(s.options[k].value===v){ has=true; break; } }



  if(!has && v){



    var o=document.createElement("option");



    o.value=v; o.textContent=v;



    s.appendChild(o);



  }



  s.value=v;



}


function refreshModelSelect(){



  var s=document.getElementById("model-select");



  if(!s) return;



  var cur=s.value || "";



  var opts=(window.MODEL_OPTIONS||[]).filter(function(x){return x && x.trim();});



  if(cur && cur!=="选择模型" && opts.indexOf(cur)===-1) opts.push(cur);



  if(!opts.length){



    s.innerHTML='<option value="">选择模型</option>';



    return;



  }



  s.innerHTML=opts.map(function(m){



    var sel=(m===cur)?" selected":"", o;



    o='<option value="'+m+'"'+sel+'>'+m+"</option>";



    return o;



  }).join("");



  if(opts.indexOf(cur)===-1) s.value=opts[0];



}





// ===== 工作空间（可折叠左侧栏）=====


function quoteJs(s){


  // 安全转义为可嵌入 HTML onclick 的单引号 JS 字符串（JSON.stringify 输出双引号，HTML 属性也用双引号 -> 不可用）


  return "'" + String(s||"").replace(/\\/g,"\\\\").replace(/'/g,"\\'") + "'";


}


function renderWorkspaces(d){


  var box=document.getElementById("ws-list"); if(!box) return;


  setWsHint("");


  if(!d || !d.list || !d.list.length){


    box.innerHTML='<div class="muted" style="padding:8px 2px">还没有工作空间，点下方按钮添加。</div>';


    return;


  }


  box.innerHTML=d.list.map(function(w){


    var cur=(w.path===(d.current||""))?" active":"";


    return '<div class="ws-item'+cur+'" onclick="switchWorkspace('+quoteJs(w.path)+')" title="'+escHtml(w.path)+'">'


         + '<span class="ws-name">'+escHtml(w.name||"")+'</span>'


         + '<button class="ws-del" onclick="event.stopPropagation(); removeWorkspace('+quoteJs(w.path)+')" title="从列表移除">×</button>'


         + '</div>';


  }).join("");


}


function wsListRefresh(){ phwCall("ws_list",{}); }


function setWsHint(t){ var h=document.getElementById("ws-hint"); if(h) h.textContent=t||""; }


function switchWorkspace(path){


  if(!path) return;


  setWsHint("切换中…");


  phwCall("ws_switch",{path:path});


}


function addWorkspace(){ setWsHint("选择文件夹…"); phwCall("ws_add",{}); }


function removeWorkspace(path){ phwCall("ws_remove",{path:path}); }





// ===== 工作空间折叠 =====


function toggleWorkspace(){


  var col=document.getElementById("workspace-col");


  if(!col) return;


  var hidden=col.classList.toggle("ws-collapsed");


  var btn=document.getElementById("ws-toggle-btn");


  if(btn) btn.textContent=hidden ? "»" : "«";


  var bar=document.getElementById("ws-collapsed-bar");


  if(bar) bar.style.display=hidden ? "flex" : "none";


}


function expandWorkspace(){


  var col=document.getElementById("workspace-col");


  if(col) col.classList.remove("ws-collapsed");


  var btn=document.getElementById("ws-toggle-btn");


  if(btn) btn.textContent="«";


  var bar=document.getElementById("ws-collapsed-bar");


  if(bar) bar.style.display="none";


}


// ===== 输入框自动增高（多行自动扩展，超长自动换行） =====

function autoGrowInput(el){

  if(!el) return;

  el.style.height = "auto";

  el.style.height = Math.min(el.scrollHeight, 120) + "px";

}

// 初始化时增高一次

(function(){
  var i=document.getElementById("chat-input");
  if(i && i.tagName === "TEXTAREA"){
    autoGrowInput(i);
    i.addEventListener("input", function(){ autoGrowInput(i); });



    initChatDragDrop();
  }
})();

// ===== 工作空间文件预览 =====
function renderWsPreview(path, files){
  var box=document.getElementById("ws-preview"); if(!box) return;
  var tt=document.getElementById("ws-preview-title"); if(tt) tt.textContent=path||"";
  var list=document.getElementById("ws-preview-files"); if(!list) return;
  list.innerHTML=(files||[]).map(function(f){
    var cls = f.dir ? "ws-pfile dir" : "ws-pfile";
    return '<div class="'+cls+'">'+escHtml(f.name)+'</div>';
  }).join("") || '<div class="muted">（空目录）</div>';
  box.style.display="block";
}
function wsPreviewEnter(){
  var tt=document.getElementById("ws-preview-title");
  if(tt && tt.textContent) switchWorkspace(tt.textContent.trim());
}








// ===== 实时耗时 ticker（工蚁卡/子任务 data-since）=====







function fmtDur(sec){



  sec=Math.max(0,Math.floor(sec));



  if(sec<60) return sec+"s";



  var m=Math.floor(sec/60), s=sec%60;



  if(m<60) return m+"m"+(s<10?"0":"")+s+"s";



  var h=Math.floor(m/60);



  return h+"h"+(m%60<10?"0":"")+(m%60)+"m";



}







setInterval(function(){



  var els=document.querySelectorAll("[data-since]");



  if(!els.length) return;



  var now=Date.now();



  for(var k=0;k<els.length;k++){



    var t=parseFloat(els[k].getAttribute("data-since"));



    if(!t) continue;



    els[k].textContent=fmtDur((now-t)/1000);



  }



},1000);











// ===== 气泡复制（事件委托，容器 innerHTML 刷新不影响）=====







(function(){



  var box=document.getElementById("chat-msgs");



  if(!box) return;



  function fallbackCopy(txt){



    var ta=document.createElement("textarea");



    ta.value=txt; ta.style.position="fixed"; ta.style.opacity="0";



    document.body.appendChild(ta); ta.select();



    try{ document.execCommand("copy"); }catch(err){}



    document.body.removeChild(ta);



  }



  box.addEventListener("click", function(e){



    var btn=e.target && e.target.closest ? e.target.closest(".msg-copy") : null;



    if(!btn) return;



    var b=btn.closest(".bubble"); if(!b) return;



    var t=b.querySelector(".bubble-text"); if(!t) return;



    var txt=t.innerText||"";



    var done=function(){ btn.classList.add("copied"); setTimeout(function(){ btn.classList.remove("copied"); },1200); };



    if(navigator.clipboard && navigator.clipboard.writeText){



      navigator.clipboard.writeText(txt).then(done, function(){ fallbackCopy(txt); done(); });



    } else { fallbackCopy(txt); done(); }



  });



})();











// ===== frameless 拖动区：顶栏交互元素豁免（pywebview 靠冒泡找 .pywebview-drag-region）=====







(function(){



  var bar=document.querySelector(".topbar");



  if(!bar) return;



  var els=bar.querySelectorAll("button,select,input,textarea,a,.win-ctrl");



  for(var k=0;k<els.length;k++){



    els[k].addEventListener("mousedown", function(e){ e.stopPropagation(); });



  }



})();











// ===== 设置页：分区切换 + 密码显隐 =====







function showSettingsPanel(key){



  var rail=document.querySelectorAll("#settings-rail .rail-item");



  for(var k=0;k<rail.length;k++){ rail[k].classList.toggle("active", rail[k].getAttribute("data-panel")===key); }



  var ps=document.querySelectorAll("#settings-panels .settings-panel");



  for(var j=0;j<ps.length;j++){ ps[j].classList.toggle("active", ps[j].getAttribute("data-panel")===key); }



}







function revealPassword(key){



  var inp=document.getElementById("set-"+key); if(!inp) return;



  var show=inp.type==="password";



  inp.type=show?"text":"password";



  var b=inp.parentNode.querySelector(".pw-eye"); if(b) b.classList.toggle("on", show);



}











// ===== 设置：服务商预设 + 打开配置目录 =====







function applyPreset(name){



  var P={



    deepseek:["https://api.deepseek.com/v1","deepseek-chat"],



    minimax:["https://api.minimaxi.com/v1","MiniMax-M2"],



    openai:["https://api.openai.com/v1","gpt-4o-mini"],



    moonshot:["https://api.moonshot.cn/v1","kimi-k2-0905-preview"],



    openrouter:["https://openrouter.ai/api/v1",""],



    ollama:["http://127.0.0.1:11434/v1","qwen3:8b"]



  }[name];



  if(!P) return;



  var b=document.getElementById("set-llm_base_url"), m=document.getElementById("set-llm_model");



  if(b && P[0]) b.value=P[0];



  if(m && P[1]) m.value=P[1];



  setVerifyHint("已填入预设，请补 API Key 后点「测试连接」","");



}







function openCfgDir(key){ phwCall("open_path",{key:key}); }



