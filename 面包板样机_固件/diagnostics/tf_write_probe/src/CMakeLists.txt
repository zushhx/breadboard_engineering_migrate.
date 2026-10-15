idf_component_register(SRCS "main.cpp" "serial_compat.cpp" "tf_test_core.cpp" INCLUDE_DIRS "."
                       REQUIRES fatfs sdmmc esp_driver_sdmmc esp_driver_spi esp_driver_uart esp_timer mbedtls)
