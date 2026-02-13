-- Database and user creation
CREATE DATABASE IF NOT EXISTS shoptet_marketplace_sync;
CREATE USER IF NOT EXISTS 'shoptet_marketplace_sync'@'%' IDENTIFIED BY 'shoptet_marketplace_sync123';
GRANT ALL PRIVILEGES ON shoptet_marketplace_sync.* TO 'shoptet_marketplace_sync'@'%';

USE shoptet_marketplace_sync;

-- Table: shoptet_stock
CREATE TABLE IF NOT EXISTS shoptet_stock (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ean              VARCHAR(32) NOT NULL,
  code             VARCHAR(64) NOT NULL,
  name             VARCHAR(255) NOT NULL,
  qty              INT NOT NULL DEFAULT 0,
  product_visibility VARCHAR(32) NULL,
  changed_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ingested_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_shoptet_stock_ean (ean),
  INDEX idx_shoptet_stock_changed (changed_at),
  INDEX idx_shoptet_stock_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Table: kaufland_unit_mapping
CREATE TABLE IF NOT EXISTS kaufland_unit_mapping (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ean               VARCHAR(32) NOT NULL,
  id_unit           VARCHAR(64) NOT NULL,
  status            VARCHAR(64) NOT NULL DEFAULT 'ACTIVE',
  last_fetch_at     DATETIME(3) NULL,
  updated_at        DATETIME(3) NOT NULL
                      DEFAULT CURRENT_TIMESTAMP(3)
                      ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_kaufland_ean (ean),
  UNIQUE KEY uq_kaufland_unit (id_unit),
  INDEX idx_kaufland_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Table: job_state
CREATE TABLE IF NOT EXISTS job_state (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  job_name        VARCHAR(64) NOT NULL,
  last_run_at     DATETIME(3) NULL,
  updated_at      DATETIME(3) NOT NULL
                   DEFAULT CURRENT_TIMESTAMP(3)
                   ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_job_state_job_name (job_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Table: jiri_models_feed_item
CREATE TABLE IF NOT EXISTS jiri_models_feed_item (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ean         VARCHAR(32) NOT NULL,
  code        VARCHAR(64) NOT NULL,
  stock       VARCHAR(32) NOT NULL,
  changed_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ingested_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uq_jiri_models_feed_item_ean (ean),
  INDEX idx_jiri_models_feed_item_code (code),
  INDEX idx_jiri_models_feed_item_changed (changed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
