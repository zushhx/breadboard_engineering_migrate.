idf_component_register(SRCS "main.cpp" "serial_compat.cpp" "tf_test_core.cpp" "modules.cpp" "recorder.cpp" "web_control.cpp" INCLUDE_DIRS "."
                       REQUIRES fatfs sdmmc esp_driver_sdmmc esp_driver_spi esp_driver_i2c esp_driver_uart esp_timer mbedtls esp_wifi esp_netif esp_event esp_http_server json)
