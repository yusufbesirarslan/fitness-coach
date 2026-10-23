/* Optional backend-web measurement is disabled (PRIV-LAUNCH-02).
   Keep the public helpers used by product flows, without sending events,
   registering analytics listeners, or storing activation markers. Landing's
   separate consent preference does not authorize this account surface. */
(function () {
  "use strict";
  function disabled() {}
  window.fxTrack = disabled;
  window.fxTrackOnce = disabled;
  window.fxActivation = disabled;
})();
