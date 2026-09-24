// 모바일(터치) 확대 막기. 두 번 탭 확대는 mobile.css 의 touch-action: manipulation 이 막는다
// — touchend 에서 빠른 두 번째 탭을 막는 코드는 넣지 말 것 (서로 다른 버튼 연타까지 먹는다).
// 이 사이트는 문서 전체가 스크롤되는 구조라 앱 셸(body 고정 · scrollTo(0,0)) 처리는 하지 않는다.
(function () {
  'use strict';
  if (!matchMedia('(pointer: coarse)').matches) return;
  ['gesturestart', 'gesturechange', 'gestureend'].forEach((t) =>
    document.addEventListener(t, (e) => e.preventDefault(), { passive: false }));
  document.addEventListener('touchmove', (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });
})();
