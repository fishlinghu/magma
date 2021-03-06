/**
 * Copyright (c) 2016-present, Facebook, Inc.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree. An additional grant
 * of patent rights can be found in the PATENTS file in the same directory.
 */
#pragma once

#include <ctime>
#include <unordered_map>
#include <memory>

#include <lte/protos/session_manager.grpc.pb.h>

#include "ServiceAction.h"

namespace magma {

/**
 * A bucket is a counter used for tracking credit volume across sessiond.
 * These are independently incremented and reset
 * Each value is in terms of a volume unit - either bytes or seconds
 */
enum Bucket {
  USED_TX = 0,
  USED_RX = 1,
  ALLOWED_TOTAL = 2,
  ALLOWED_TX = 3,
  ALLOWED_RX = 4,
  REPORTING_TX = 5,
  REPORTING_RX = 6,
  REPORTED_TX = 7,
  REPORTED_RX = 8,
  MAX_VALUES = 9,
};

enum ReAuthState {
  REAUTH_NOT_NEEDED = 0,
  REAUTH_REQUIRED = 1,
  REAUTH_PROCESSING = 2,
};

enum ServiceState {
  SERVICE_ENABLED = 0,
  SERVICE_NEEDS_DEACTIVATION = 1,
  SERVICE_DISABLED = 2,
  SERVICE_NEEDS_ACTIVATION = 3
};

enum CreditUpdateType {
  CREDIT_NO_UPDATE = 0,
  CREDIT_QUOTA_EXHAUSTED = 1,
  CREDIT_VALIDITY_TIMER_EXPIRED = 2,
  CREDIT_REAUTH_REQUIRED = 3
};

/**
 * SessionCredit tracks all the credit volumes associated with a charging key
 * for a user. It can receive used credit, add allowed credit, and check if
 * there is an update (quota exhausted, etc)
 */
class SessionCredit {
public:
  struct Usage {
    uint64_t bytes_tx;
    uint64_t bytes_rx;
  };

  SessionCredit();

  SessionCredit(ServiceState start_state);

  /**
   * add_used_credit increments USED_TX and USED_RX
   * as being recently updated
   */
  void add_used_credit(uint64_t used_tx, uint64_t used_rx);

  /**
   * reset_reporting_credit resets the REPORTING_* to 0 when there is some kind
   * of error in reporting. After this, during the next update the credit will
   * become eligible to update once again.
   */
  void reset_reporting_credit();

  /**
   * Credit update has failed to the OCS, so mark this credit as failed so it
   * can be cut off accordingly
   */
  void mark_failure();

  /**
   * receive_credit increments ALLOWED* and moves the REPORTING_* credit to
   * the REPORTED_* credit
   */
  void receive_credit(
    uint64_t total_volume,
    uint64_t tx_volume,
    uint64_t rx_volume,
    uint32_t validity_time,
    bool is_final);

  /**
   * get_update_type returns the type of update required for the credit. If no
   * update is required, it returns CREDIT_NO_UPDATE. Else, it returns an update
   * type
   */
  CreditUpdateType get_update_type();

  /**
   * get_update returns a filled-in CreditUsage if an update exists, and a blank
   * one if no update exists. Check has_update before calling.
   * This method also sets the REPORTING_* credit buckets
   */
  SessionCredit::Usage get_usage_for_reporting(bool is_termination);

  /**
   * get_action returns the action to take on the credit based on the last
   * update. If no action needs to take place, CONTINUE_SERVICE is returned.
   */
  ServiceActionType get_action();

  /**
   * Returns true if either of REPORTING_* buckets are more than 0
   */
  bool is_reporting();

  /**
   * Helper function to get the credit in a particular bucket
   */
  uint64_t get_credit(Bucket bucket) const;

  /**
   * Mark the credit to be in the REAUTH_REQUIRED state. The next time
   * get_update is called, this credit will report its usage.
   */
  void reauth();

  /**
   * Limit for the total usage (tx + rx) in credit updates.
   * If the used counts are greater than the limits, then the credit
   * updates will be sent over multiple transactions.
   */
  static uint64_t USAGE_REPORTING_LIMIT;

private:
  bool reporting_;
  bool is_final_;
  ReAuthState reauth_state_;
  ServiceState service_state_;
  std::time_t expiry_time_;
  uint64_t buckets_[MAX_VALUES];

private:
  bool quota_exhausted();

  bool max_overage_reached();

  bool validity_timer_expired();

  void set_expiry_time(uint32_t validity_time);

  bool is_reauth_required();
};

}
