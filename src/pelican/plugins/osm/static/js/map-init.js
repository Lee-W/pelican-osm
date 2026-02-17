document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".map").forEach(el => {
    requestAnimationFrame(() => {
      const lat = parseFloat(el.dataset.lat);
      const lon = parseFloat(el.dataset.lon);
      const name = el.dataset.name;

      const map = L.map(el, { scrollWheelZoom: false }).setView([lat, lon], 16);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map);
      L.marker([lat, lon]).addTo(map).bindPopup(name);
    });
  });
});
