
"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";

function element(tag, className = "", value = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(value);
    return node;
}

async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
        throw new Error(`${url} returned HTTP ${response.status}`);
    }
    return response.json();
}

function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
    ? "Unknown"
    : date.toLocaleString();
}

function monitorStatus(monitor) {
    const timestamp = Date.parse(monitor.checked_at);
    const age = Date.now() - timestamp;

  // Do not present old or invalid results as current health.
    if (!Number.isFinite(timestamp) || age < -60000 || age > 15 * 60 * 1000) {
        return "STALE";
    }

    return monitor.status === "UP" ? "UP" : "DOWN";
}

function sparkline(history) {
    const values = history
    .filter((item) => item.status === "UP")
    .map((item) => Number(item.latency_ms))
    .filter((value) => Number.isFinite(value))
    .reverse();

    if (!values.length) {
        return element("p", "muted", "No successful latency samples");
    }

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 300 70");
    svg.setAttribute("class", "sparkline");
    svg.setAttribute("role", "img");
    svg.setAttribute(
        "aria-label",
        `Latency trend across ${values.length} successful checks`
    );

    const low = Math.min(...values);
    const high = Math.max(...values);
    const span = Math.max(high - low, 1);

    const points = values.map((value, index) => {
        const x = values.length === 1 ? 150 : 5 + (index / (values.length - 1)) * 290;
        const y = 60 - ((value - low) / span) * 50;
        return [x, y];
    });

    if (points.length > 1) {
        const line = document.createElementNS(SVG_NS, "polyline");
        line.setAttribute(
            "points",
            points.map(([x, y]) => `${x},${y}`).join(" ")
        );
        line.setAttribute("fill", "none");
        line.setAttribute("stroke", "#55b7ff");
        line.setAttribute("stroke-width", "2.5");
        line.setAttribute("stroke-linejoin", "round");
        svg.append(line);
    } else {
        const dot = document.createElementNS(SVG_NS, "circle");
        dot.setAttribute("cx", String(points[0][0]));
        dot.setAttribute("cy", String(points[0][1]));
        dot.setAttribute("r", "4");
        dot.setAttribute("fill", "#55b7ff");
        svg.append(dot);
    }

    return svg;
}

function monitorCard(monitor, history) {
    const status = monitorStatus(monitor);
    const card = element("article", "monitor");
    const top = element("div", "monitor-top");
    const heading = element("div");

    heading.append(
        element("h3", "", monitor.id),
        element("p", "muted", monitor.type.toUpperCase())
    );

    top.append(
        heading,
        element("span", `status ${status.toLowerCase()}`, status)
    );

    card.append(top);

    const latency = Number(monitor.latency_ms);
    card.append(element(
        "div",
        "monitor-latency",
        Number.isFinite(latency) ? `${latency.toFixed(2)} ms` : "—"
    ));

    card.append(sparkline(history));

    if (history.length) {
        const successful = history.filter(
        (item) => item.status === "UP"
        ).length;

        const percent = (successful / history.length) * 100;

        card.append(element(
            "p",
            "muted",
            `${percent.toFixed(1)}% successful — last ${history.length} checks`
        ));
    } else {
        card.append(element("p", "muted", "History unavailable"));
    }

    card.append(element(
        "p",
        "muted",
        `Last checked: ${formatDate(monitor.checked_at)}`
    ));

    return card;
}

function renderMetrics(monitors) {
    const states = monitors.map(monitorStatus);

    $("metric-total").textContent = String(monitors.length);
    $("metric-up").textContent =
        String(states.filter((x) => x === "UP").length);
    $("metric-down").textContent =
        String(states.filter((x) => x === "DOWN").length);
    $("metric-stale").textContent =
        String(states.filter((x) => x === "STALE").length);
}

function renderIncidents(events) {
    const container = $("incidents");

    if (!events.length) {
        container.replaceChildren(
        element("p", "muted", "No incidents recorded yet.")
        );
        return;
    }

    const rows = events.slice(0, 8).map((event) => {
        const row = element("div", "incident");
        const details = element("div");

        details.append(
            element("strong", "", event.target_id),
            element("p", "muted", event.message || "State changed"),
            element("p", "muted", formatDate(event.occurred_at))
        );

        const kind = event.event_type === "DOWN" ? "down" : "up";
        row.append(
            details,
            element("span", `status ${kind}`, event.event_type)
        );

        return row;
    });

    container.replaceChildren(...rows);
}

let loading = false;

async function refreshDashboard() {
    if (loading) return;
    loading = true;
    $("refresh").disabled = true;

    try {
        const [monitorsData, incidentsData] = await Promise.all([
            getJson("/api/monitors"),
            getJson("/api/incidents?limit=8"),
        ]);

        const monitors = monitorsData.monitors;
        renderMetrics(monitors);
        renderIncidents(incidentsData.incidents);

        const cards = await Promise.all(
            monitors.map(async (monitor) => {
                let history = [];

                try {
                    const data = await getJson(`/api/monitors/${encodeURIComponent(monitor.id)}/history?limit=30`);
                    history = data.history;
                } catch (error) {
                    console.warn("Could not load monitor history:", error);
                }

                return monitorCard(monitor, history);
            })
        );

        $("monitors").replaceChildren(...(cards.length ? cards : [element("p", "muted", "No checks recorded yet.")]));

        $("updated").textContent = `Updated: ${new Date().toLocaleTimeString()}`;

        $("error").hidden = true;
    } catch (error) {
        $("error").textContent = `Dashboard could not load monitoring data: ${error.message}`;
        $("error").hidden = false;
        $("updated").textContent = "Update failed";
        // Preserve previously displayed data, if any.
    } finally {
        $("refresh").disabled = false;
        loading = false;
    }
}

$("refresh").addEventListener("click", refreshDashboard);
refreshDashboard();
setInterval(refreshDashboard, 30000);
