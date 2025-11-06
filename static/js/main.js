// --------------------------
// CONFIGURATION
// --------------------------

// Replace with your Firebase config
const firebaseConfig = {
    apiKey: "YOUR_FIREBASE_API_KEY",
    authDomain: "YOUR_PROJECT.firebaseapp.com",
    databaseURL: "https://YOUR_PROJECT.firebaseio.com",
    projectId: "YOUR_PROJECT",
    storageBucket: "YOUR_PROJECT.appspot.com",
    messagingSenderId: "YOUR_SENDER_ID",
    appId: "YOUR_APP_ID"
};

// Replace with your Google Maps API key
const GOOGLE_MAPS_API_KEY = "YOUR_GOOGLE_MAPS_API_KEY";

// --------------------------
// INITIALIZE FIREBASE
// --------------------------
const app = firebase.initializeApp(firebaseConfig);
const database = firebase.database();

// --------------------------
// INITIALIZE GOOGLE MAP
// --------------------------
let map;
let markers = [];

function initMap() {
    const center = { lat: 23.7806, lng: 90.2794 }; // Example: Dhaka
    map = new google.maps.Map(document.getElementById("map"), {
        zoom: 13,
        center: center
    });

    // Sample marker at center
    new google.maps.Marker({
        position: center,
        map,
        title: "Demo Center"
    });

    // Load existing reports from Firebase
    const reportsRef = database.ref('reports');
    reportsRef.on('child_added', snapshot => {
        const report = snapshot.val();
        if (report.lat && report.lng) {
            const marker = new google.maps.Marker({
                position: { lat: report.lat, lng: report.lng },
                map: map,
                title: report.category || "Report"
            });
            markers.push(marker);
        }
    });
}

// Dynamically load Google Maps API
(function loadGoogleMaps() {
    if (!GOOGLE_MAPS_API_KEY || GOOGLE_MAPS_API_KEY.includes("YOUR_")) return;
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&callback=initMap`;
    script.async = true;
    script.defer = true;
    document.head.appendChild(script);
})();

// --------------------------
// SOS BUTTON LOGIC
// --------------------------
const sosBtn = document.getElementById("sos-test-btn");
const confirmSOS = document.getElementById("confirmSOS");

sosBtn.addEventListener("click", () => {
    const modal = new bootstrap.Modal(document.getElementById("sosModal"));
    modal.show();
});

confirmSOS.addEventListener("click", () => {
    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser.");
        return;
    }

    confirmSOS.innerText = "Sending...";
    navigator.geolocation.getCurrentPosition(pos => {
        const sosData = {
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            accuracy: pos.coords.accuracy,
            timestamp: pos.timestamp
        };

        // Push SOS to Firebase
        const sosRef = database.ref('sos');
        sosRef.push(sosData)
            .then(() => alert("SOS sent successfully!"))
            .catch(err => alert("Error sending SOS: " + err.message))
            .finally(() => {
                confirmSOS.innerText = "Yes, send SOS";
                bootstrap.Modal.getInstance(document.getElementById("sosModal")).hide();
            });
    }, err => {
        alert("Could not get location: " + err.message);
        confirmSOS.innerText = "Yes, send SOS";
    }, { enableHighAccuracy: true, timeout: 10000 });
});

// --------------------------
// REPORT FORM LOGIC
// --------------------------
const reportForm = document.getElementById("reportForm");

reportForm.addEventListener("submit", ev => {
    ev.preventDefault();
    if (!navigator.geolocation) { alert("Geolocation not supported."); return; }

    const category = document.getElementById("reportCategory").value;
    const description = document.getElementById("reportDescription").value;

    navigator.geolocation.getCurrentPosition(pos => {
        const reportData = {
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            category: category,
            description: description,
            timestamp: pos.timestamp
        };

        database.ref('reports').push(reportData)
            .then(() => {
                alert("Report submitted successfully!");
                bootstrap.Modal.getInstance(document.getElementById("reportModal")).hide();
                reportForm.reset();
            })
            .catch(err => alert("Error submitting report: " + err.message));
    }, err => {
        alert("Could not get location: " + err.message);
    }, { enableHighAccuracy: false, timeout: 10000 });
});
