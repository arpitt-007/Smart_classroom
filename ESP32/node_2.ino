#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_wifi.h"

const char* WIFI_SSID = "Arpit’s iPhone";
const char* WIFI_PASSWORD = "arpit007";

const char* NODE_ID = "NODE_02";

const char* AP_SSID = "SMART_CLASS_NODE_02";
const char* AP_PASSWORD = "classroom123";

const char* SERVER_URL = "http://172.20.10.4:8000/api/node";

void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("=================================");
  Serial.println(" SMART CLASSROOM - NODE_02");
  Serial.println("=================================");

  WiFi.mode(WIFI_AP_STA);

  Serial.print("Connecting to iPhone hotspot");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("Connected to iPhone hotspot!");

  Serial.print("STA IP: ");
  Serial.println(WiFi.localIP());

  if (WiFi.softAP(AP_SSID, AP_PASSWORD)) {

    Serial.println();
    Serial.println("Classroom AP started!");

    Serial.print("SSID: ");
    Serial.println(AP_SSID);

    Serial.print("AP IP: ");
    Serial.println(WiFi.softAPIP());

  } else {

    Serial.println("Failed to start classroom AP!");

  }
}

void loop() {

  wifi_sta_list_t stationList;

  esp_err_t result =
      esp_wifi_ap_get_sta_list(&stationList);

  Serial.println();
  Serial.println("========== NODE STATUS ==========");

  Serial.print("Node: ");
  Serial.println(NODE_ID);

  Serial.print("Connected devices: ");
  Serial.println(stationList.num);

  for (int i = 0; i < stationList.num; i++) {

    char macString[18];

    sprintf(
      macString,
      "%02X:%02X:%02X:%02X:%02X:%02X",
      stationList.sta[i].mac[0],
      stationList.sta[i].mac[1],
      stationList.sta[i].mac[2],
      stationList.sta[i].mac[3],
      stationList.sta[i].mac[4],
      stationList.sta[i].mac[5]
    );

    Serial.println();
    Serial.println("----- DEVICE DETECTED -----");

    Serial.print("MAC Address: ");
    Serial.println(macString);

    if (WiFi.status() == WL_CONNECTED) {

      HTTPClient http;

      http.begin(SERVER_URL);

      http.addHeader(
        "Content-Type",
        "application/json"
      );

      String jsonData =
        "{\"node_id\":\"" + String(NODE_ID) +
        "\",\"device_mac\":\"" + String(macString) +
        "\",\"status\":\"detected\"}";

      Serial.println("Sending to backend:");
      Serial.println(jsonData);

      int responseCode =
          http.POST(jsonData);

      Serial.print("HTTP Response: ");
      Serial.println(responseCode);

      if (responseCode > 0) {

        String response = http.getString();

        Serial.print("Server response: ");
        Serial.println(response);

      } else {

        Serial.print("HTTP Error: ");
        Serial.println(
          http.errorToString(responseCode)
        );
      }

      http.end();
    }
  }

  if (stationList.num == 0) {
    Serial.println("No student devices connected.");
  }

  delay(5000);
}