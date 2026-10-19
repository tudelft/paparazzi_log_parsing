(function () {
  try {
    if (localStorage.getItem('pprzNight') === '1')
      document.documentElement.classList.add('night');
  } catch (e) {}
  window.toggleNight = function () {
    const root = document.documentElement;
    root.classList.toggle('night');
    try { localStorage.setItem('pprzNight', root.classList.contains('night') ? '1' : '0'); } catch (e) {}
    window.dispatchEvent(new Event('nightchange'));
  };
})();
