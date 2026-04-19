(() => {
  // ----------------------------
  // Small helpers
  // ----------------------------
  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

async function fetchWithTimeout(url, options = {}, timeout = 480000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

  async function readJsonOrText(res) {
    const text = await res.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    return { json, text };
  }

  // ----------------------------
  // Hamburger toggle
  // ----------------------------
  const hamburger = document.getElementById("hamburger");
  const menu = document.getElementById("menu");

  function toggleMenu() {
    if (!menu) return;
    const isCollapsed = menu.classList.contains("collapsed");
    menu.classList.toggle("collapsed", !isCollapsed);
    menu.classList.toggle("expanded", isCollapsed);
    if (hamburger) hamburger.setAttribute("aria-expanded", String(isCollapsed));
  }

  if (hamburger) {
    hamburger.addEventListener("click", toggleMenu);
    hamburger.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleMenu();
      }
    });
  }

  // ----------------------------
  // Plotly Map
  // ----------------------------
  async function loadWorldMap() {
    const el = document.getElementById("world-map");
    if (!el) return;

    try {
      const res = await fetch("/api/world-map");
      if (!res.ok) throw new Error(`Failed to load /api/world-map (HTTP ${res.status})`);

      const fig = await res.json();
      Plotly.newPlot(el, fig.data, fig.layout, {
        displayModeBar: false,
        scrollZoom: false,
        responsive: true,
      });
    } catch (err) {
      console.error(err);
    }
  }

  // ----------------------------
  // AI Generate
  // ----------------------------
  async function wireAiGenerate() {
  const form = document.getElementById("ai-form");
  const btn = document.getElementById("ai-generate-btn");
  const statusEl = document.getElementById("ai-status");
  if (!form || !btn) return;

  btn.addEventListener("click", async (e) => {
    e.preventDefault();

    const oldText = btn.textContent;
    btn.disabled = true;
    btn.style.opacity = "0.6";
    btn.textContent = "Generating…";
    if (statusEl) statusEl.textContent = "Working… (this can take 1–4 minutes)";

    try {
      // 1) Start job (short request)
      const startRes = await fetchWithTimeout(
        form.action,
        {
          method: "POST",
          body: new FormData(form),
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
          },
        },
        30000
      );

      const { json: startJson, text: startText } = await readJsonOrText(startRes);
      if (!startRes.ok || !startJson?.job_id) {
        throw new Error(
          (startJson && (startJson.error || startJson.detail)) ||
          (startText && startText.slice(0, 300)) ||
          `HTTP ${startRes.status}`
        );
      }

      const pollUrl = startJson.poll_url || `/admin/ai-jobs/${startJson.job_id}`;

      // 2) Poll status
      const pollDeadline = Date.now() + 10 * 60 * 1000; // 10 minutes
      while (Date.now() < pollDeadline) {
        if (statusEl) statusEl.textContent = "Working… (checking progress)";

        const pollRes = await fetchWithTimeout(
          pollUrl,
          { headers: { "Accept": "application/json" } },
          15000
        );

        const { json: pollJson, text: pollText } = await readJsonOrText(pollRes);
        if (!pollRes.ok || !pollJson) {
          throw new Error(
            (pollJson && (pollJson.error || pollJson.detail)) ||
            (pollText && pollText.slice(0, 300)) ||
            `HTTP ${pollRes.status}`
          );
        }

        if (pollJson.status === "done") {
          const result = pollJson.result;
          if (!result) throw new Error("Job completed but no result returned.");

          if (statusEl) statusEl.textContent = "Done ✅ Saved to DB";

          if (result.hero_image_url && statusEl) {
            const img = document.createElement("img");
            img.src = result.hero_image_url;
            img.alt = "Generated hero image";
            img.style.maxWidth = "400px";
            img.style.display = "block";
            img.style.marginTop = "12px";
            statusEl.appendChild(img);
          }
          return;
        }

        if (pollJson.status === "error") {
          throw new Error(pollJson.error || "AI job failed.");
        }

        await new Promise((r) => setTimeout(r, 2000));
      }

      throw new Error("Timed out waiting for AI job to finish.");
    } catch (err) {
      console.error(err);
      const msg =
        err?.name === "AbortError"
          ? "Request timed out (JS aborted)."
          : (err?.message || String(err));

      if (statusEl) statusEl.textContent = "Error: " + msg;
      alert("AI generation failed: " + msg);
    } finally {
      btn.disabled = false;
      btn.style.opacity = "1";
      btn.textContent = oldText;
      setTimeout(() => {
        if (statusEl) statusEl.textContent = "";
      }, 240000);
    }
  });
}
  // ----------------------------
  // Refresh Sources (Part 2)
  // ----------------------------
  async function wireRefreshSources() {
    const refreshBtn = document.getElementById("refresh-sources-btn");
    const sourcesStatus = document.getElementById("sources-status");
    const sourcesList = document.getElementById("sources-list");
    if (!refreshBtn) return;

    function renderSources(facts) {
      if (!sourcesList) return;

      if (!facts || facts.length === 0) {
        sourcesList.innerHTML = "<em>No sources found yet.</em>";
        return;
      }

      sourcesList.innerHTML = facts
        .map((f) => {
          const title = escapeHtml(f.title || "");
          const url = String(f.url || "");
          const safeUrl = escapeHtml(url);
          const snippet = escapeHtml(f.snippet || "");

          return `
            <div style="padding:8px 0;border-bottom:1px solid #eee;">
              <div><strong>${title}</strong></div>
              ${url ? `<div><a href="${safeUrl}" target="_blank" rel="noopener">${safeUrl}</a></div>` : ""}
              <div style="color:#555;">${snippet}</div>
            </div>
          `;
        })
        .join("");
    }

    refreshBtn.addEventListener("click", async (e) => {
      e.preventDefault();

      const pageId = window.PAGE_ID;
      if (!pageId) {
        const msg =
          "PAGE_ID missing. In admin_page_form.html add BEFORE app.js:\n" +
          "<script>window.PAGE_ID = {{ page.id|tojson }};</script>";
        console.error(msg);
        alert(msg);
        return;
      }

      refreshBtn.disabled = true;
      if (sourcesStatus) sourcesStatus.textContent = "Fetching sources…";

      try {
        const topicInput = document.querySelector('input[name="topic"]');
        const topic = (topicInput?.value || "").trim();

        const url = `/admin/pages/${pageId}/refresh-sources`;
        console.log("Refreshing sources via:", url);

        const res = await fetch(url, {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          },
          body: new URLSearchParams({ topic }),
        });

        const { json, text } = await readJsonOrText(res);

        if (!res.ok) {
          const errMsg =
            (json && (json.error || json.detail)) ||
            `HTTP ${res.status} calling ${url}`;
          console.error("Refresh sources failed:", res.status, text);
          throw new Error(errMsg);
        }

        if (!json) {
          throw new Error("Server returned an empty or non-JSON response.");
        }

        if (sourcesStatus) {
          sourcesStatus.textContent = `Done ✅ Added ${json.inserted ?? 0} sources`;
        }
        renderSources(json.facts || []);
      } catch (err) {
        const msg = err?.message || String(err);
        if (sourcesStatus) sourcesStatus.textContent = `Error: ${msg}`;
        alert("Source fetch failed: " + msg);
      } finally {
        refreshBtn.disabled = false;
        setTimeout(() => {
          if (sourcesStatus) sourcesStatus.textContent = "";
        }, 8000);
      }
    });
  }

  // ----------------------------
  // DOM Ready
  // ----------------------------
  document.addEventListener("DOMContentLoaded", () => {
    loadWorldMap();
    wireAiGenerate();
    wireRefreshSources();
  });
})();




const useLocationBtn = document.getElementById("use-location-btn");
const locationStatus = document.getElementById("location-status");
const latInput = document.getElementById("search-lat");
const lngInput = document.getElementById("search-lng");

useLocationBtn.addEventListener("click", () => {

    if (!navigator.geolocation) {
        locationStatus.textContent = "Geolocation not supported";
        return;
    }

    locationStatus.textContent = "Getting your location...";

    navigator.geolocation.getCurrentPosition((position) => {

    document.getElementById("search-lat").value = position.coords.latitude;
    document.getElementById("search-lng").value = position.coords.longitude;

        locationStatus.textContent = "Location ready";

        console.log("LAT:", latInput.value);
        console.log("LNG:", lngInput.value);

    });
});


async function uploadImage() {
  const fileInput = document.getElementById("imageUpload");
  const file = fileInput.files[0];

  console.log("SELECTED FILE:", file);

  if (!file) {
    alert("Select an image first");
    return;
  }

  const formData = new FormData();
  formData.append("image", file);

  const response = await fetch("/admin/upload-image", {
    method: "POST",
    body: formData
  });

  console.log("UPLOAD RESPONSE STATUS:", response.status);

  const data = await response.json();
  console.log("UPLOAD RESPONSE DATA:", data);

  if (data.url) {
    document.getElementById("card_image_url").value = data.url;
    console.log("CARD IMAGE URL FILLED:", data.url);
  } else {
    alert("Upload failed");
  }
}




async function uploadImage() {

    const fileInput = document.getElementById("imageUpload")
    const file = fileInput.files[0]

    const formData = new FormData()
    formData.append("image", file)

    const response = await fetch("/admin/upload-image", {
        method: "POST",
        body: formData
    })

    const data = await response.json()

    if (data.success) {

        document.getElementById("card_image_url").value = data.url;

        alert("Image uploaded successfully!")

    }
}



if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition(function(position) {

    const lat = position.coords.latitude
    const lng = position.coords.longitude

    const url = new URL(window.location)

    if (!url.searchParams.get("lat")) {
      url.searchParams.set("lat", lat)
      url.searchParams.set("lng", lng)

      window.location = url
    }

  })
}



const markers = {};

results.forEach((item, index) => {

  const marker = new google.maps.Marker({
    position: position,
    map: map,
    title: item.name
  });

  markers[index] = marker;

  marker.addListener("click", () => {
    const card = document.querySelector(`[data-result-index="${index}"]`);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  });

});


const markerCluster = new markerClusterer.MarkerClusterer({
  map,
  markers
});


const iconMap = {
  coffee: "/static/icons/coffee.png",
  restaurant: "/static/icons/restaurant.png",
  gym: "/static/icons/gym.png"
};

const marker = new google.maps.Marker({
  position,
  map,
  icon: iconMap[item.category] || null
});



  window.addEventListener("scroll", function () {
    const stickyMap = document.querySelector(".search-map-sticky");
    if (!stickyMap) return;

    if (window.scrollY > 40) {
      stickyMap.classList.add("is-scrolled");
    } else {
      stickyMap.classList.remove("is-scrolled");
    }
  });


  setTimeout(() => {
  google.maps.event.trigger(map, "resize");
  map.setCenter(center);
}, 300);





  async function getCurrentPositionForAi() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) {
        resolve({ lat: null, lng: null });
        return;
      }

      navigator.geolocation.getCurrentPosition(
        (position) => {
          resolve({
            lat: position.coords.latitude,
            lng: position.coords.longitude
          });
        },
        () => {
          resolve({ lat: null, lng: null });
        },
        { enableHighAccuracy: true, timeout: 8000 }
      );
    });
  }

  async function runAiSearch() {
    const input = document.getElementById("ai-chat-submit");
    const status = document.getElementById("ai-search-status");
    const answer = document.getElementById("ai-search-answer");

    if (!input || !status || !answer) return;

    const message = input.value.trim();
    if (!message) {
      status.textContent = "Type a question first.";
      return;
    }

    status.textContent = "Thinking...";
    answer.innerHTML = "";

    const coords = await getCurrentPositionForAi();

    try {
      const response = await fetch("/ai-search", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          message: message,
          lat: coords.lat,
          lng: coords.lng
        })
      });

      const data = await response.json();

      if (!response.ok) {
        status.textContent = data.error || "Something went wrong.";
        return;
      }

      status.textContent = "";
      answer.innerHTML = `
        <div class="ai-search-response">
          <p>${data.summary}</p>
        </div>
      `;

      console.log("AI results:", data);

      // Optional:
      // 1. Replace visible cards with data.internal_results / data.external_results
      // 2. Rebuild your map markers using data.map_results
      // 3. Scroll user to results area
    } catch (error) {
      status.textContent = "There was a problem running the AI search.";
      console.error(error);
    }
  }

  document.getElementById("ai-chat-submit")?.addEventListener("click", runAiSearch);



  const resultsEl = document.getElementById("ai-results");

if (resultsEl) {
  const cards = [...(data.internal_results || []), ...(data.external_results || [])];

  resultsEl.innerHTML = cards.map((item) => {
    const image = item.photo_url || item.card_image_url || "/static/img/default-business.jpg";
    const title = item.name || "Untitled";
    const address = item.address || "";
    const website = item.website
      ? `<a href="${item.website}" target="_blank" rel="noopener" class="btn-secondary">Visit website</a>`
      : "";

    return `
      <article class="search-result-card">
        <div class="search-result-card__image-wrap">
          <img src="${image}" alt="${title}" class="search-result-card__image">
        </div>
        <div class="search-result-card__body">
          <h3 class="search-result-card__title">${title}</h3>
          <div class="search-result-card__address">${address}</div>
          <div class="search-result-card__actions">
            ${website}
          </div>
        </div>
      </article>
    `;
  }).join("");
}




document.addEventListener("click", function (event) {
  const prevBtn = event.target.closest(".carousel-btn--prev");
  const nextBtn = event.target.closest(".carousel-btn--next");
  if (!prevBtn && !nextBtn) return;

  const gallery = event.target.closest("[data-carousel]");
  if (!gallery) return;

  const slides = Array.from(gallery.querySelectorAll(".search-result-card__gallery-slide"));
  const currentIndex = slides.findIndex(slide => slide.classList.contains("is-active"));
  if (currentIndex === -1 || slides.length <= 1) return;

  slides[currentIndex].classList.remove("is-active");

  let nextIndex = currentIndex;
  if (nextBtn) nextIndex = (currentIndex + 1) % slides.length;
  if (prevBtn) nextIndex = (currentIndex - 1 + slides.length) % slides.length;

  slides[nextIndex].classList.add("is-active");
});




document.addEventListener("click", function (event) {
  const prevBtn = event.target.closest(".carousel-btn--prev");
  const nextBtn = event.target.closest(".carousel-btn--next");

  if (!prevBtn && !nextBtn) return;

  const gallery = event.target.closest("[data-carousel]");
  if (!gallery) return;

  const slides = Array.from(
    gallery.querySelectorAll(".search-result-card__gallery-slide")
  );

  const currentIndex = slides.findIndex(slide =>
    slide.classList.contains("is-active")
  );

  if (currentIndex === -1 || slides.length <= 1) return;

  slides[currentIndex].classList.remove("is-active");

  let nextIndex = currentIndex;

  if (nextBtn) {
    nextIndex = (currentIndex + 1) % slides.length;
  }

  if (prevBtn) {
    nextIndex = (currentIndex - 1 + slides.length) % slides.length;
  }

  slides[nextIndex].classList.add("is-active");
});
