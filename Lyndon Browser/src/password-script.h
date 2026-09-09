/* password-script.h — the script injected into pages to work login forms.
 *
 * Shared verbatim by both builds. Only the way it talks back to the browser
 * differs: WebKit gives each handler its own object under
 * window.webkit.messageHandlers, WebView2 gives one postMessage for
 * everything and the message says which kind it is. Keeping the rest in one
 * file is what stops the two from drifting — this is security-relevant code
 * and two copies of it would be two behaviours.
 */
#pragma once

#ifdef _WIN32
/* WebView2 takes a string or a JSON value; a JSON string keeps the shape the
 * WebKit side already posts, and tab.c routes on .type exactly the same way. */
# define LY_PW_TRANSPORT \
"if(!window.chrome||!window.chrome.webview)return;" \
"var H={postMessage:function(m){" \
"  try{window.chrome.webview.postMessage(JSON.stringify(" \
"    {channel:'lyndonPasswords',body:m}));}catch(_){}}};"
#else
# define LY_PW_TRANSPORT \
"var H=window.webkit&&window.webkit.messageHandlers&&" \
"      window.webkit.messageHandlers.lyndonPasswords;" \
"if(!H)return;"
#endif

/* Runs at document-end in the top frame only. Never in subframes: a
 * third-party iframe that could read a filled password would defeat the
 * point of having a password manager at all. */
static const char *LY_PASSWORD_SCRIPT =
"(function(){'use strict';"
"if(window.__lyndonPw)return;"
"Object.defineProperty(window,'__lyndonPw',{value:1});"
LY_PW_TRANSPORT

"var vis=function(e){return !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);};"
/* Shallow scan — cheap enough to repeat on every batch of mutations. */
"var pws=function(){return [].slice.call("
"  document.querySelectorAll('input[type=password]')).filter(vis);};"

/* Deep scan, shadow roots included. Walking every element is far too costly to
 * do on a timer, so both callers below spend from a small fixed budget: web
 * components are worth supporting, but not at the price of a slow page. */
"var walk=function(root,sel,out,depth){"
" if(depth>4)return;"
" try{[].push.apply(out,[].slice.call(root.querySelectorAll(sel)));}catch(_){}"
" var all;try{all=root.querySelectorAll('*');}catch(_){return;}"
" for(var i=0;i<all.length;i++)if(all[i].shadowRoot)walk(all[i].shadowRoot,sel,out,depth+1);};"
"var deep=function(sel){var out=[];walk(document,sel,out,0);return out.filter(vis);};"

"var USERRE=/(user|login|e-?mail|account|ident|nick|handle|loginfmt|sign-?in)/i;"
"var textish=function(e){var t=(e.type||'').toLowerCase();"
" return ['text','email','tel',''].indexOf(t)>=0;};"
"var userish=function(e){"
" if(!textish(e))return false;"
" var a=(e.getAttribute('autocomplete')||'').toLowerCase();"
" if(a.indexOf('username')>=0||a==='email')return true;"
" return USERRE.test([e.name,e.id,e.getAttribute('aria-label'),e.placeholder].join(' '));};"

/* The username is almost always the nearest preceding text-ish input in the
 * same form; a few sites put it after, so fall back to looking forward. A
 * field that names itself beats either, which is what makes two-field forms
 * with a stray text input in between come out right. */
"var userFor=function(p){"
" var f=p.form;"
" if(f){var fi=[].slice.call(f.querySelectorAll('input'));"
"  for(var k=0;k<fi.length;k++)if(vis(fi[k])&&userish(fi[k])&&fi[k].value)return fi[k];}"
" var scope=f||document;"
" var ins=[].slice.call(scope.querySelectorAll('input'));"
" var at=ins.indexOf(p);"
" for(var i=at-1;i>=0;i--){if(textish(ins[i])&&vis(ins[i]))return ins[i];}"
" for(var j=at+1;j<ins.length;j++){if(textish(ins[j])&&vis(ins[j]))return ins[j];}"
" return null;};"

/* Frameworks track their own state, so writing .value directly is invisible to
 * them. Going through the prototype setter and firing the events is what makes
 * React and friends notice the fill. */
"var setVal=function(e,v){"
" try{var d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
" if(d&&d.set)d.set.call(e,v);else e.value=v;}catch(_){e.value=v;}"
" e.dispatchEvent(new Event('input',{bubbles:true}));"
" e.dispatchEvent(new Event('change',{bubbles:true}));};"

"window.__lyndonFill=function(u,p){"
" var f=pws();if(!f.length)f=deep('input[type=password]');"
" if(!f.length)return false;"
" var pw=f[0],uf=userFor(pw);"
" if(uf&&u)setVal(uf,u);"
" setVal(pw,p);"
" return true;};"

/* Does this submission look like a sign-in at all? Only asked before offering
 * to fill in a gap, where a false positive costs the user an interruption. */
"var loginish=function(el){"
" var f=el&&el.form;"
" var hay=(location.pathname+' '+location.search+' '+"
"  (f?[f.getAttribute('action'),f.id,f.className,f.name].join(' '):'')).toLowerCase();"
" if(/(log-?in|sign-?in|sign-?on|auth|session|account|anmeld|connexion|bejelentkez)/.test(hay))"
"   return true;"
" if((el.getAttribute('autocomplete')||'').toLowerCase().indexOf('username')>=0)return true;"
" var scope=f||document,btns;"
" try{btns=scope.querySelectorAll('button,input[type=submit],[role=button]');}catch(_){return false;}"
" for(var i=0;i<btns.length&&i<40;i++){"
"  var t=((btns[i].innerText||btns[i].value||'')+'').trim().toLowerCase();"
"  if(t&&t.length<24&&/^(next|continue|sign ?in|log ?in|submit|proceed)$/.test(t))return true;}"
" return false;};"

"var rdeep=0,last='';"
"var report=function(){"
" var n=pws().length;"
" if(n===0&&rdeep<2){rdeep++;n=deep('input[type=password]').length;}"
" var key=n+'|'+location.origin;"
" if(key===last)return;last=key;"
" H.postMessage({type:'forms',count:n,origin:location.origin});};"

"var cdeep=0,sent=0;"
"var capture=function(){"
" if(sent>3)return;"
" var f=pws();"
" if(!f.length&&cdeep<6){cdeep++;f=deep('input[type=password]');}"
" if(f.length){"
"  var pw=f[0],uf=userFor(pw);"
"  if(pw.value){sent++;"
"   H.postMessage({type:'submit',origin:location.origin,"
"                  username:uf?uf.value:'',password:pw.value});return;}"
/* A password box that is there but reads back empty: the site cleared it in
 * its own submit handler, or never bound it to the node we can see. Everything
 * else about the submission still says a login happened. */
"  if(uf&&uf.value&&userish(uf)&&loginish(uf)){sent++;"
"   H.postMessage({type:'partial',origin:location.origin,"
"                  username:uf.value,hasPassword:true});}"
"  return;}"
/* No password field at all — the first screen of a two-step login. Worth
 * remembering the name for the screen that follows, but not worth interrupting
 * anyone over: the password has not been asked for yet. */
" var ins=[].slice.call(document.querySelectorAll('input')).filter(vis);"
" if(!ins.length&&cdeep<6){cdeep++;ins=deep('input');}"
" for(var i=0;i<ins.length;i++){"
"  if(!userish(ins[i])||!ins[i].value||!loginish(ins[i]))continue;"
"  sent++;"
"  H.postMessage({type:'partial',origin:location.origin,"
"                 username:ins[i].value,hasPassword:false});"
"  return;}};"

"document.addEventListener('submit',capture,true);"
/* Single-page logins frequently never fire a submit event. */
"document.addEventListener('click',function(e){"
" var t=e.target;if(!t||!t.closest)return;"
" if(t.closest('button,input[type=submit],input[type=button],[role=button]'))"
"   setTimeout(capture,0);},true);"
"document.addEventListener('keydown',function(e){"
" if(e.key==='Enter')setTimeout(capture,0);},true);"

"report();"
/* Debounced: login forms often appear well after first paint, but a
 * mutation-per-message would flood the UI process. */
"var t=null;"
"try{new MutationObserver(function(){"
" if(t)return;t=setTimeout(function(){t=null;report();},400);"
"}).observe(document.documentElement,{childList:true,subtree:true});}catch(_){}"
"})();";
