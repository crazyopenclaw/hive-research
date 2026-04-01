<template>
  <div id="app">
    <router-view />
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
#app {
  font-family: 'JetBrains Mono', 'Space Grotesk', monospace;
  -webkit-font-smoothing: antialiased;
  color: #111;
  background: #fafafa;
}

/* Squid accent colors */
:root {
  --squid-ink: #1a1a2e;
  --squid-tentacle: #16213e;
  --squid-body: #0f3460;
  --squid-highlight: #e94560;
  --squid-glow: #533483;
}
</style>
