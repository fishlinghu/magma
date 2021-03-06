/*
 * Licensed to the OpenAirInterface (OAI) Software Alliance under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The OpenAirInterface Software Alliance licenses this file to You under
 * the Apache License, Version 2.0  (the "License"); you may not use this file
 * except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *-------------------------------------------------------------------------------
 * For more information about the OpenAirInterface (OAI) Software Alliance:
 *      contact@openairinterface.org
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/types.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <errno.h>

#include "assertions.h"
#include "log.h"
#include "common_defs.h"
#include "gtpv1u.h"
#include "ControllerMain.h"

extern struct gtp_tunnel_ops gtp_tunnel_ops;

int openflow_uninit(void)
{
  int ret;
  if ((ret = stop_of_controller()) < 0) {
    OAILOG_ERROR(LOG_GTPV1U, "Could not stop openflow controller on uninit\n");
  }
  return ret;
}

int openflow_init(
  struct in_addr *ue_net,
  uint32_t mask,
  int mtu,
  int *fd0,
  int *fd1u)
{
  AssertFatal(
    start_of_controller() >= 0, "Could not start openflow controller\n");
  return 0;
}

int openflow_reset(void)
{
  int rv = 0;
  return rv;
}

int openflow_add_tunnel(
  struct in_addr ue,
  struct in_addr enb,
  uint32_t i_tei,
  uint32_t o_tei,
  Imsi_t imsi)
{
  return openflow_controller_add_gtp_tunnel(
    ue, enb, i_tei, o_tei, (const char *) imsi.digit);
}

int openflow_del_tunnel(struct in_addr ue, uint32_t i_tei, uint32_t o_tei)
{
  return openflow_controller_del_gtp_tunnel(ue, i_tei);
}

int openflow_discard_data_on_tunnel(struct in_addr ue, uint32_t i_tei)
{
  return openflow_controller_discard_data_on_tunnel(ue, i_tei);
}

int openflow_forward_data_on_tunnel(struct in_addr ue, uint32_t i_tei)
{
  return openflow_controller_forward_data_on_tunnel(ue, i_tei);
}

static const struct gtp_tunnel_ops openflow_ops = {
  .init = openflow_init,
  .uninit = openflow_uninit,
  .reset = openflow_reset,
  .add_tunnel = openflow_add_tunnel,
  .del_tunnel = openflow_del_tunnel,
  .discard_data_on_tunnel = openflow_discard_data_on_tunnel,
  .forward_data_on_tunnel = openflow_forward_data_on_tunnel,
};

const struct gtp_tunnel_ops *gtp_tunnel_ops_init_openflow(void)
{
  return &openflow_ops;
}
