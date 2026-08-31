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
"var pws=function(){return [].slice.call("
"  document.querySelectorAll('input[type=password]')).filter(vis);};"

/* The username is almost always the nearest preceding text-ish input in the
 * same form; a few sites put it after, so fall back to looking forward. */
"var userFor=function(p){"
" var scope=p.form||document;"
" var ins=[].slice.call(scope.querySelectorAll('input'));"
" var at=ins.indexOf(p),ok=['text','email','tel',''];"
" for(var i=at-1;i>=0;i--){if(ok.indexOf((ins[i].type||'').toLowerCase())>=0&&vis(ins[i]))return ins[i];}"
" for(var j=at+1;j<ins.length;j++){if(ok.indexOf((ins[j].type||'').toLowerCase())>=0&&vis(ins[j]))return ins[j];}"
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
" var f=pws();if(!f.length)return false;"
" var pw=f[0],uf=userFor(pw);"
" if(uf&&u)setVal(uf,u);"
" setVal(pw,p);"
" return true;};"

"var last='';"
"var report=function(){"
" var n=pws().length;var key=n+'|'+location.origin;"
" if(key===last)return;last=key;"
" H.postMessage({type:'forms',count:n,origin:location.origin});};"

"var capture=function(){"
" var f=pws();if(!f.length)return;"
" var pw=f[0];if(!pw.value)return;"
" var uf=userFor(pw);"
" H.postMessage({type:'submit',origin:location.origin,"
"                username:uf?uf.value:'',password:pw.value});};"

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
