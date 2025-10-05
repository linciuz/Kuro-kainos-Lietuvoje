// Kuro Kainos Lietuvoje - Application Logic
// Version: 1.0.0

let appState = {
    city: 'Vilnius',
    location: null,
    fuelType: 'petrol95',
    sortBy: 'price',
    stations: []
};

const cityCoordinates = {
    'Vilnius': { lat: 54.6872, lon: 25.2797 },
    'Kaunas': { lat: 54.8985, lon: 23.9036 },
    'Klaipėda': { lat: 55.7033, lon: 21.1443 },
    'Šiauliai': { lat: 55.9349, lon: 23.3135 },
    'Panevėžys': { lat: 55.7348, lon: 24.3570 },
    'Ukmergė': { lat: 55.2500, lon: 24.7500 },
    'Alytus': { lat: 54.3963, lon: 24.0458 },
    'Marijampolė': { lat: 54.5594, lon: 23.3544 }
};

// Station data - In production, replace with real API
const allStations = {
    'Vilnius': [
        { id: 1, brand: 'viada', name: 'Viada', address: 'Kalvarijų g. 125', petrol95: 1.419, diesel: 1.409, lpg: 0.739, updated: 'prieš 1 val.', lat: 54.6872, lon: 25.2797 },
        { id: 2, brand: 'circlek', name: 'Circle K', address: 'Savanorių pr. 178', petrol95: 1.429, diesel: 1.419, lpg: 0.749, updated: 'prieš 2 val.', lat: 54.6892, lon: 25.2817 },
        { id: 3, brand: 'neste', name: 'Neste', address: 'Ukmergės g. 234', petrol95: 1.439, diesel: 1.429, lpg: 0.759, updated: 'prieš 30 min.', lat: 54.6912, lon: 25.2837 },
        { id: 4, brand: 'emsi', name: 'Emsi', address: 'Žirmūnų g. 68', petrol95: 1.399, diesel: 1.389, lpg: 0.719, updated: 'prieš 3 val.', lat: 54.6932, lon: 25.2857 },
        { id: 5, brand: 'baltic', name: 'Baltic Petroleum', address: 'Geležinio Vilko g. 12', petrol95: 1.409, diesel: 1.399, lpg: 0.729, updated: 'prieš 45 min.', lat: 54.6852, lon: 25.2777 },
    ],
    'Kaunas': [
        { id: 11, brand: 'viada', name: 'Viada', address: 'Savanorių pr. 255', petrol95: 1.425, diesel: 1.415, lpg: 0.735, updated: 'prieš 1 val.', lat: 54.8985, lon: 23.9036 },
        { id: 12, brand: 'circlek', name: 'Circle K', address: 'Pramonės pr. 16', petrol95: 1.435, diesel: 1.425, lpg: 0.745, updated: 'prieš 2 val.', lat: 54.9005, lon: 23.9056 },
        { id: 13, brand: 'baltic', name: 'Baltic Petroleum', address: 'Jonavos g. 88', petrol95: 1.415, diesel: 1.405, lpg: 0.725, updated: 'prieš 45 min.', lat: 54.9025, lon: 23.9076 },
        { id: 14, brand: 'neste', name: 'Neste', address: 'Taikos pr. 45', petrol95: 1.445, diesel: 1.435, lpg: 0.755, updated: 'prieš 3 val.', lat: 54.8965, lon: 23.9016 },
    ],
    'Klaipėda': [
        { id: 21, brand: 'neste', name: 'Neste', address: 'Taikos pr. 145', petrol95: 1.445, diesel: 1.435, lpg: 0.755, updated: 'prieš 1 val.', lat: 55.7033, lon: 21.1443 },
        { id: 22, brand: 'circlek', name: 'Circle K', address: 'Baltijos pr. 67', petrol95: 1.455, diesel: 1.445, lpg: 0.765, updated: 'prieš 3 val.', lat: 55.7053, lon: 21.1463 },
        { id: 23, brand: 'viada', name: 'Viada', address: 'Minijos g. 89', petrol95: 1.430, diesel: 1.420, lpg: 0.740, updated: 'prieš 2 val.', lat: 55.7013, lon: 21.1423 },
    ],
    'Šiauliai': [
        { id: 31, brand: 'viada', name: 'Viada', address: 'Tilžės g. 109', petrol95: 1.429, diesel: 1.419, lpg: 0.739, updated: 'prieš 2 val.', lat: 55.9349, lon: 23.3135 },
        { id: 32, brand: 'orlen', name: 'Orlen', address: 'Vilniaus g. 245', petrol95: 1.449, diesel: 1.439, lpg: 0.759, updated: 'prieš 1 val.', lat: 55.9369, lon: 23.3155 },
        { id: 33, brand: 'emsi', name: 'Emsi', address: 'Gegužių g. 34', petrol95: 1.415, diesel: 1.405, lpg: 0.735, updated: 'prieš 4 val.', lat: 55.9329, lon: 23.3115 },
    ],
    'Panevėžys': [
        { id: 41, brand: 'emsi', name: 'Emsi', address: 'Smėlynės g. 88', petrol95: 1.409, diesel: 1.399, lpg: 0.729, updated: 'prieš 1 val.', lat: 55.7348, lon: 24.3570 },
        { id: 42, brand: 'circlek', name: 'Circle K', address: 'Klaipėdos g. 134', petrol95: 1.439, diesel: 1.429, lpg: 0.749, updated: 'prieš 4 val.', lat: 55.7368, lon: 24.3590 },
        { id: 43, brand: 'viada', name: 'Viada', address: 'Marijonų g. 12', petrol95: 1.420, diesel: 1.410, lpg: 0.740, updated: 'prieš 2 val.', lat: 55.7328, lon: 24.3550 },
    ],
    'Ukmergė': [
        { id: 51, brand: 'viada', name: 'Viada', address: 'Vilniaus g. 45', petrol95: 1.415, diesel: 1.405, lpg: 0.735, updated: 'prieš 1 val.', lat: 55.2500, lon: 24.7500 },
        { id: 52, brand: 'circlek', name: 'Circle K', address: 'Kauno g. 78', petrol95: 1.425, diesel: 1.415, lpg: 0.745, updated: 'prieš 2 val.', lat: 55.2520, lon: 24.7520 },
        { id: 53, brand: 'emsi', name: 'Emsi', address: 'Deltuvos g. 12', petrol95: 1.405, diesel: 1.395, lpg: 0.725, updated: 'prieš 30 min.', lat: 55.2540, lon: 24.7540 },
        { id: 54, brand: 'baltic', name: 'Baltic Petroleum', address: 'Panevėžio g. 34', petrol95: 1.410, diesel: 1.400, lpg: 0.730, updated: 'prieš 3 val.', lat: 55.2480, lon: 24.7480 },
    ],
    'Alytus': [
        { id: 61, brand: 'neste', name: 'Neste', address: 'Naujoji g. 67', petrol95: 1.435, diesel: 1.425, lpg: 0.745, updated: 'prieš 2 val.', lat: 54.3963, lon: 24.0458 },
        { id: 62, brand: 'viada', name: 'Viada', address: 'Pulko g. 23', petrol95: 1.420, diesel: 1.410, lpg: 0.740, updated: 'prieš 1 val.', lat: 54.3983, lon: 24.0478 },
        { id: 63, brand: 'circlek', name: 'Circle K', address: 'Jotvingių g. 56', petrol95: 1.440, diesel: 1.430, lpg: 0.750, updated: 'prieš 3 val.', lat: 54.3943, lon: 24.0438 },
    ],
    'Marijampolė': [
        { id: 71, brand: 'circlek', name: 'Circle K', address: 'Kauno g. 89', petrol95: 1.430, diesel: 1.420, lpg: 0.750, updated: 'prieš 1 val.', lat: 54.5594, lon: 23.3544 },
        { id: 72, brand: 'emsi', name: 'Emsi', address: 'Vilniaus g. 156', petrol95: 1.410, diesel: 1.400, lpg: 0.730, updated: 'prieš 3 val.', lat: 54.5614, lon: 23.3564 },
        { id: 73, brand: 'baltic', name: 'Baltic Petroleum', address: 'Laisvės a. 34', petrol95: 1.425, diesel: 1.415, lpg: 0.745, updated: 'prieš 2 val.', lat: 54.5574, lon: 23.3524 },
    ]
};

function selectCity(city) {
    appState.city = city;
    appState.location = cityCoordinates[city];
    
    document.querySelectorAll('.city-btn').forEach(btn => btn.classList.remove('active'));
    const cityBtn = document.getElementById(`city-${city}`);
    if (cityBtn) cityBtn.classList.add('active');
    
    loadStations();
}

function tryGPS() {
    if (!navigator.geolocation) {
        showInfo('GPS nepalaiko jūsų naršyklė. Pasirinkite miestą rankiniu būdu.');
        return;
    }

    showInfo('Nustatoma GPS vieta...');
    
    navigator.geolocation.getCurrentPosition(
        (position) => {
            appState.location = {
                lat: position.coords.latitude,
                lon: position.coords.longitude
            };
            
            const nearest = findNearestCity(position.coords.latitude, position.coords.longitude);
            selectCity(nearest);
            
            document.getElementById('gps-btn').classList.add('active');
            showInfo(`✅ GPS vieta nustatyta! Artimiausias miestas: ${nearest}`);
        },
        (error) => {
            let errorMsg = 'GPS nepavyko. ';
            if (error.code === 1) {
                errorMsg += 'Leiskite prieigą prie vietos naršyklės nustatymuose.';
            } else {
                errorMsg += 'Pasirinkite miestą rankiniu būdu.';
            }
            showInfo(errorMsg);
        },
        { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 }
    );
}

function findNearestCity(lat, lon) {
    let nearest = 'Vilnius';
    let minDistance = Infinity;
    
    for (const [city, coords] of Object.entries(cityCoordinates)) {
        const distance = Math.sqrt(Math.pow(lat - coords.lat, 2) + Math.pow(lon - coords.lon, 2));
        if (distance < minDistance) {
            minDistance = distance;
            nearest = city;
        }
    }
    return nearest;
}

function loadStations() {
    appState.stations = allStations[appState.city] || [];
    
    if (appState.location) {
        appState.stations.forEach(station => {
            station.distance = calculateDistance(
                appState.location.lat, appState.location.lon,
                station.lat, station.lon
            );
        });
    } else {
        appState.stations.forEach(station => { station.distance = 0; });
    }
    
    renderStations();
}

function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
             Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
             Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return (R * c).toFixed(1);
}

function renderStations() {
    const container = document.getElementById('stations-list');
    const sorted = getSortedStations();
    const stats = getPriceStats();

    let html = '';

    if (stats) {
        html += `
            <div class="price-comparison">
                <div class="price-stats">
                    <div class="price-stat">
                        <div class="price-stat-label">Pigiausia</div>
                        <div class="price-stat-value lowest">€${stats.lowest.toFixed(3)}</div>
                    </div>
                    <div class="price-stat">
                        <div class="price-stat-label">Vidutinė</div>
                        <div class="price-stat-value">€${stats.average.toFixed(3)}</div>
                    </div>
                    <div class="price-stat">
                        <div class="price-stat-label">Brangiausia</div>
                        <div class="price-stat-value highest">€${stats.highest.toFixed(3)}</div>
                    </div>
                </div>
            </div>
        `;
    }

    sorted.forEach((station, index) => {
        const price = station[appState.fuelType];
        const isBest = index === 0 && appState.sortBy === 'price';

        html += `
            <div class="station-card">
                ${isBest ? '<div class="best-price-badge">⭐ GERIAUSIA KAINA</div>' : ''}
                <span class="station-brand brand-${station.brand}">${station.name}</span>
                <div class="station-header">
                    <h3 class="station-name">${station.name}</h3>
                    <div>
                        <span class="station-price">€${price.toFixed(3)}</span>
                        <span class="price-unit">/L</span>
                    </div>
                </div>
                <p class="station-address">${station.address}</p>
                <div class="station-footer">
                    <span class="station-distance">📍 ${station.distance} km</span>
                    <span class="station-updated">🕐 ${station.updated}</span>
                    <button class="station-directions" onclick="openDirections(${station.lat}, ${station.lon})">
                        🗺️ Maršrutas
                    </button>
                </div>
            </div>
        `;
    });

    container.innerHTML = html || '<div class="loading">Nėra degalinių šiame mieste</div>';
}

function getSortedStations() {
    return [...appState.stations].sort((a, b) => {
        if (appState.sortBy === 'price') {
            return a[appState.fuelType] - b[appState.fuelType];
        } else {
            return parseFloat(a.distance) - parseFloat(b.distance);
        }
    });
}

function getPriceStats() {
    if (appState.stations.length === 0) return null;
    const prices = appState.stations.map(s => s[appState.fuelType]);
    return {
        lowest: Math.min(...prices),
        highest: Math.max(...prices),
        average: prices.reduce((a, b) => a + b, 0) / prices.length
    };
}

function selectFuel(type) {
    appState.fuelType = type;
    document.querySelectorAll('.fuel-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`btn-${type}`).classList.add('active');
    renderStations();
}

function sortStations(sortBy) {
    appState.sortBy = sortBy;
    document.querySelectorAll('.sort-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`sort-${sortBy}`).classList.add('active');
    renderStations();
}

function openDirections(lat, lon) {
    window.open(`https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`, '_blank');
}

function showInfo(message) {
    const container = document.getElementById('info-container');
    container.innerHTML = `<div class="info-message">ℹ️ ${message}</div>`;
    setTimeout(() => { container.innerHTML = ''; }, 6000);
}

// Initialize app on page load
window.addEventListener('load', () => {
    selectCity('Vilnius');
});
