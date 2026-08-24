import json
import dash
from dash import html
import pandas as pd

# -------------------------------
# DATA
# -------------------------------

data = {
    "City": ["Johnston", "West Des Moines", "Ankeny", "Perry", "Naperville", "Prague", "Florence", "Naples", "Vienna", "Zurich", "Islamorada"],
    "State": ["Iowa", "Iowa", "Iowa", "Iowa", "Illinois", "Prague", "Italy", "Italy", "Austria", "Switzerland", "Florida"],
    "Country": ["USA", "USA", "USA", "USA", "USA", "Czech Republic", "Italy", "Italy", "Austria", "Switzerland", "Florida"],
    "Name": ["Eway Corp", "Mediacom Telecommunications", "Etech Solutions", "Dallas County Hospital", "Wi-Tronix", "Prague Trip", "Florence Trip", "Naples Trip", "Vienna Trip", "Zurich Trip", "Islamorada Trip"],
    "Latitude": [41.6739, 41.5772, 41.7318, 41.8383, 41.7508, 50.0755, 43.8029, 40.85, 48.2082, 47.3769, 24.9243],
    "Longitude": [-93.6977, -93.7113, -93.6001, -94.1072, -88.1535, 14.4378, 11.2558, 14.26, 16.3738, 8.5417, -80.7560],
}

# -------------------------------
# Cloudinary setup
# -------------------------------
# TODO: replace with your Cloudinary cloud name (found on your Cloudinary dashboard)
CLOUDINARY_CLOUD_NAME = "dcfqyqhvr"

# Each city now gets a list of 5 photo slots for the click-to-open collage.
# Each slot can be EITHER a bare public ID (e.g. "localai/feed/yykelzo8ejyiawqunqrz")
# OR a full Cloudinary URL — cloudinary_url() handles both. Replace the
# remaining "TODO_..." placeholders with your real public IDs/URLs as you
# upload photos for each location.
cloudinary_public_ids = {
    "Johnston": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1777232234/localai/feed/yykelzo8ejyiawqunqrz.jpg",
        "TODO_johnston_2",
        "TODO_johnston_3",
        "TODO_johnston_4",
        "TODO_johnston_5",
    ],
    "West Des Moines": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1777233479/localai/feed/vcythv20nndntsulyauh.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1777233479/localai/feed/vcythv20nndntsulyauh.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1777233479/localai/feed/vcythv20nndntsulyauh.jpg",
        "TODO_west_des_moines_4",
        "TODO_west_des_moines_5",
    ],
    "Ankeny": [
        "TODO_ankeny_1", "TODO_ankeny_2", "TODO_ankeny_3", "TODO_ankeny_4", "TODO_ankeny_5",
    ],
    "Perry": [
        "TODO_perry_1", "TODO_perry_2", "TODO_perry_3", "TODO_perry_4", "TODO_perry_5",
    ],
    "Naperville": [
        "TODO_naperville_1", "TODO_naperville_2", "TODO_naperville_3", "TODO_naperville_4", "TODO_naperville_5",
    ],
    "Prague": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549695/IMG_6525_hwyc2h.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549705/IMG_6609_ash5j1.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549706/IMG_6516_zjt9g5.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549710/IMG_6548_o7xkih.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549711/IMG_6518_zyx40b.jpg",
    ],
    
    "Prague": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549695/IMG_6525_hwyc2h.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549705/IMG_6609_ash5j1.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549706/IMG_6516_zjt9g5.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549710/IMG_6548_o7xkih.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785549711/IMG_6518_zyx40b.jpg",
    ],
    "Florence": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552484/IMG_3626_ibhxfo.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552492/IMG_3654_g3ikzs.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552492/IMG_3629_iksdwl.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552494/IMG_3658_sawwhy.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552494/IMG_3645_rw9t0w.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785552727/IMG_3641_z1t1gu.jpg",
    ],
    "Naples": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553117/IMG_0315_ziz05q.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553125/IMG_0323_tssnlf.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553135/IMG_0301_mtvlef.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553136/IMG_0340_yekkqg.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553138/IMG_0387_fkr2ti.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553139/IMG_0350_fqipq7.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553141/IMG_0365_kcaf14.jpg",
    ],
    "Naples": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553117/IMG_0315_ziz05q.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553125/IMG_0323_tssnlf.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553135/IMG_0301_mtvlef.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553136/IMG_0340_yekkqg.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553138/IMG_0387_fkr2ti.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553139/IMG_0350_fqipq7.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553141/IMG_0365_kcaf14.jpg",
    ],
    "Vienna": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553783/IMG_3714_llxdbt.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553793/IMG_5423_kx6koe.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553794/IMG_5476_tswm7q.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553796/IMG_5422_uoo6yg.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553800/IMG_3754_anbutn.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553804/IMG_3730_bmjiaz.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553810/IMG_5634_yh4jfl.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553817/IMG_5686_fblfxd.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553820/IMG_5640_fnj0bj.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553829/IMG_3762_reyxsr.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553838/IMG_5498_x0kfcv.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553842/IMG_5583_q8nmz0.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553845/IMG_5826_rq5w8o.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785553847/IMG_6745_bfomve.jpg",
        
    ],
    "Zurich": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554772/IMG_4521_w92ba4.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554799/IMG_4463_r9bkq5.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554799/IMG_4541_jtzt0w.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554800/IMG_4576_wpchvd.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554800/IMG_4466_xuu9ha.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554810/IMG_4519_s32b6w.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785554816/IMG_4522_w9kvgd.jpg",
        
    ],
    "Islamorada": [
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555259/IMG_4030_ex24wp.jpg", 
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555266/IMG_4052_tiwey1.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555273/IMG_4035_irj81m.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555274/IMG_4044_bop2c3.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555277/IMG_4042_coefp6.jpg",
        "https://res.cloudinary.com/dcfqyqhvr/image/upload/v1785555281/IMG_7678_oqgrnc.jpg",
        
    ]
}


def cloudinary_url(public_id: str) -> str:
    """Build a Cloudinary delivery URL from a public ID, or pass through a full URL as-is."""
    if public_id.startswith("http://") or public_id.startswith("https://"):
        return public_id
    return f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/image/upload/{public_id}"


data["ImageURLs"] = [
    [cloudinary_url(pid) for pid in cloudinary_public_ids[city]]
    for city in data["City"]
]

# -------------------------------
# Build DataFrame ONCE
# -------------------------------
df = pd.DataFrame(data)

# -------------------------------
# SANITY CHECK + FAIL FAST
# -------------------------------
print("\n=== SANITY CHECK ===")
print("Type of df:", type(df))
print("Columns:", df.columns.tolist())
print("\nFirst 3 rows:")
print(df.head(3))
print("\nNull counts:")
print(df.isnull().sum())
print("====================\n")

required = {"City", "Country", "Latitude", "Longitude", "ImageURLs"}
missing = required - set(df.columns)
if missing:
    raise ValueError(f"DataFrame missing columns: {missing}. Current columns: {df.columns.tolist()}")

# -------------------------------
# Mapbox setup
# -------------------------------
# NOTE: Plotly's chart wrapper for Mapbox cannot render a true 3D globe
# (it only supports a flat map), so this app embeds Mapbox GL JS directly.
# Mapbox GL JS natively supports a spinnable "globe" projection while
# still using your Mapbox token/style.
MAPBOX_TOKEN = "pk.eyJ1IjoiZG1hbnMyNyIsImEiOiJjbWthbGM2YnIwazExM2RwcXJqbDJsOXQxIn0.RTiS-2auBvtfYdrwEMouxA"
MAPBOX_STYLE = "mapbox://styles/mapbox/light-v11"

locations_json = json.dumps(df.to_dict("records"))

# -------------------------------
# Dash app
# -------------------------------
app = dash.Dash(__name__)

app.enable_dev_tools(
    debug=False,
    dev_tools_ui=False,
    dev_tools_props_check=False,
    dev_tools_hot_reload=False
)

# Dash still needs a layout, even though the map itself is built by the
# raw Mapbox GL JS script below (outside of Dash's normal component tree).
app.layout = html.Div()

app.index_string = f"""
<!DOCTYPE html>
<html>
<head>
    {{%metas%}}
    <title>{{%title%}}</title>
    {{%favicon%}}
    {{%css%}}
    <link href="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.css" rel="stylesheet" />
    <script src="https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js"></script>
    <style>
        html, body {{ margin: 0; padding: 0; height: 100%; }}
        #mapbox-globe {{ position: absolute; top: 0; bottom: 0; width: 100%; height: 600px; }}

        .location-popup {{
            background: rgba(0, 0, 0, 0.8);
            color: white;
            border-radius: 8px;
            padding: 6px 10px;
        }}
        .location-popup .popup-title {{ font-weight: 600; white-space: nowrap; }}
        .mapboxgl-popup-content {{ background: transparent; box-shadow: none; padding: 0; }}
        .mapboxgl-popup-tip {{ display: none; }}

        .collage-overlay {{
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.75);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 3000;
        }}
        .collage-overlay.open {{ display: flex; }}
        .collage-modal {{
            background: #161616;
            border-radius: 12px;
            padding: 20px;
            max-width: 900px;
            width: 90%;
            max-height: 85vh;
            overflow-y: auto;
            position: relative;
        }}
        .collage-title {{
            color: white;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 14px;
            padding-right: 30px;
        }}
        .collage-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 10px;
        }}
        .collage-grid img {{
            width: 100%;
            height: 160px;
            object-fit: cover;
            border-radius: 8px;
            background: #333;
        }}
        .collage-close {{
            position: absolute;
            top: 10px;
            right: 14px;
            color: white;
            font-size: 26px;
            line-height: 1;
            cursor: pointer;
            background: none;
            border: none;
        }}
    </style>
</head>
<body>
    <div id="mapbox-globe"></div>

    <div class="collage-overlay" id="collage-overlay">
        <div class="collage-modal">
            <button class="collage-close" id="collage-close">&times;</button>
            <div class="collage-title" id="collage-title"></div>
            <div class="collage-grid" id="collage-grid"></div>
        </div>
    </div>

    {{%app_entry%}}
    <footer>
        {{%config%}}
        {{%scripts%}}
        {{%renderer%}}
    </footer>
    <script>
        mapboxgl.accessToken = "{MAPBOX_TOKEN}";
        const locations = {locations_json};

        const map = new mapboxgl.Map({{
            container: "mapbox-globe",
            style: "{MAPBOX_STYLE}",
            projection: "globe",
            center: [-91.5, 41.7],
            zoom: 2.2
        }});

        map.addControl(new mapboxgl.NavigationControl());

        map.on("style.load", () => {{
            map.setFog({{}});
        }});

        // -------------------------------
        // Collage modal (shared by every dot)
        // -------------------------------
        const collageOverlay = document.getElementById("collage-overlay");
        const collageTitle = document.getElementById("collage-title");
        const collageGrid = document.getElementById("collage-grid");
        const collageClose = document.getElementById("collage-close");

        function openCollage(loc) {{
            collageTitle.textContent = `${{loc.City}} — ${{loc.Country}}`;
            collageGrid.innerHTML = (loc.ImageURLs || [])
                .map((url) => `<img src="${{url}}" onerror="this.style.display='none'" />`)
                .join("");
            collageOverlay.classList.add("open");
        }}

        function closeCollage() {{
            collageOverlay.classList.remove("open");
        }}

        collageClose.addEventListener("click", closeCollage);
        collageOverlay.addEventListener("click", (e) => {{
            if (e.target === collageOverlay) closeCollage();
        }});
        document.addEventListener("keydown", (e) => {{
            if (e.key === "Escape") closeCollage();
        }});

        // -------------------------------
        // Markers: hover shows a small title tooltip, click opens the collage
        // -------------------------------
        locations.forEach((loc) => {{
            const el = document.createElement("div");
            el.style.width = "14px";
            el.style.height = "14px";
            el.style.borderRadius = "50%";
            el.style.background = "#5B5FEF";
            el.style.border = "2px solid white";
            el.style.boxShadow = "0 0 4px rgba(0,0,0,0.4)";
            el.style.cursor = "pointer";

            const hoverPopup = new mapboxgl.Popup({{
                offset: 16,
                closeButton: false,
                closeOnClick: false
            }}).setHTML(`
                <div class="location-popup">
                    <div class="popup-title">${{loc.City}} — ${{loc.Country}}</div>
                </div>
            `);

            new mapboxgl.Marker(el)
                .setLngLat([loc.Longitude, loc.Latitude])
                .addTo(map);

            el.addEventListener("mouseenter", () => {{
                hoverPopup.setLngLat([loc.Longitude, loc.Latitude]).addTo(map);
            }});
            el.addEventListener("mouseleave", () => {{
                hoverPopup.remove();
            }});
            el.addEventListener("click", () => {{
                openCollage(loc);
            }});
        }});
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    # Development mode (no production warning)
    app.run(host="127.0.0.1", port=8505, debug=False,
        dev_tools_ui=False,
        dev_tools_props_check=False)