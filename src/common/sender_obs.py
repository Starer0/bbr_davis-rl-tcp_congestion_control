# Copyright 2019 Nathan Jay and Noga Rotman
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np

# The monitor interval class used to pass data from the PCC subsystem to
# the machine learning module.
#
class SenderMonitorInterval():
    def __init__(self,
                 sender_id,
                 send_ratio=0.0,
                 loss=0.0,
                 rtt_in=0.0,
                 mrtt=0.0,
                 thupt=0.0,
                 during=0.0,
                 rtt_avg=0.0):
        self.features = {}
        self.sender_id = sender_id
        self.send_ratio = send_ratio
        self.loss = loss
        self.rtt_in = rtt_in
        self.mrtt = mrtt
        self.thupt = thupt
        self.during = during
        self.rtt_avg = rtt_avg

    def get(self, feature):
        if feature in self.features.keys():
            return self.features[feature]
        else:
            result = SenderMonitorIntervalMetric.eval_by_name(feature, self)
            self.features[feature] = result
            return result

    # Convert the observation parts of the monitor interval into a numpy array
    def as_array(self, features):
        return np.array([self.get(f) / SenderMonitorIntervalMetric.get_by_name(f).scale for f in features])

class SenderHistory():
    def __init__(self, length, features, sender_id):
        self.features = features
        self.values = []
        self.sender_id = sender_id
        for i in range(0, length):
            self.values.append(SenderMonitorInterval(self.sender_id))

    def step(self, new_mi):
        self.values.pop(0)
        self.values.append(new_mi)

    def as_array(self):
        arrays = []
        for mi in self.values:
            arrays.append(mi.as_array(self.features))
        arrays = np.array(arrays).flatten()
        return arrays

class SenderMonitorIntervalMetric():
    _all_metrics = {}

    def __init__(self, name, func, min_val, max_val, scale=1.0):
        self.name = name
        self.func = func
        self.min_val = min_val
        self.max_val = max_val
        self.scale = scale
        SenderMonitorIntervalMetric._all_metrics[name] = self

    def eval(self, mi):
        return self.func(mi)

    def eval_by_name(name, mi):
        return SenderMonitorIntervalMetric._all_metrics[name].eval(mi)

    def get_by_name(name):
        return SenderMonitorIntervalMetric._all_metrics[name]

def get_min_obs_vector(feature_names):
    result = []
    for feature_name in feature_names:
        feature = SenderMonitorIntervalMetric.get_by_name(feature_name)
        result.append(feature.min_val)
    return np.array(result) 

def get_max_obs_vector(feature_names):
    result = []
    for feature_name in feature_names:
        feature = SenderMonitorIntervalMetric.get_by_name(feature_name)
        result.append(feature.max_val)
    return np.array(result) 

def _mi_metric_recv_rate(mi):
    if mi.during==0:
        return 0.0
    #print(mi.thupt)
    return mi.thupt / 5

def _mi_metric_recv_dur(mi):
    return mi.during

def _mi_metric_avg_latency(mi):
    #print(mi.rtt_avg/1000000)
    return mi.rtt_avg/1000000

def _mi_metric_send_rate(mi):
    return 0.0

def _mi_metric_send_dur(mi):
    return 0.0

def _mi_metric_loss_ratio(mi):
    #print(mi.loss/1000)
    return mi.loss/1000

def _mi_metric_latency_increase(mi):
    return 0.0

def _mi_metric_ack_latency_inflation(mi):
    if mi.during==0:
        return 0.0
    return mi.rtt_in/(mi.during*100000)

def _mi_metric_sent_latency_inflation(mi):
    if mi.during==0:
        return 0.0
    #print(mi.rtt_in/(mi.during*10000))
    return mi.rtt_in/(mi.during*10000)

_conn_min_latencies = {}
def _mi_metric_conn_min_latency(mi):
    return mi.mrtt/1000
        
    
def _mi_metric_send_ratio(mi):
    return mi.send_ratio/1000

def _mi_metric_latency_ratio(mi):  
    if mi.mrtt==0:
        return 1.0 
    return mi.rtt_avg/mi.mrtt

SENDER_MI_METRICS = [
    SenderMonitorIntervalMetric("send rate", _mi_metric_send_rate, 0.0, 1e9, 1e7),
    SenderMonitorIntervalMetric("recv rate", _mi_metric_recv_rate, 0.0, 1e9, 1e7),
    SenderMonitorIntervalMetric("recv dur", _mi_metric_recv_dur, 0.0, 100.0),
    SenderMonitorIntervalMetric("send dur", _mi_metric_send_dur, 0.0, 100.0),
    SenderMonitorIntervalMetric("avg latency", _mi_metric_avg_latency, 0.0, 100.0),
    SenderMonitorIntervalMetric("loss ratio", _mi_metric_loss_ratio, 0.0, 1.0),
    SenderMonitorIntervalMetric("ack latency inflation", _mi_metric_ack_latency_inflation, -1.0, 10.0),
    SenderMonitorIntervalMetric("sent latency inflation", _mi_metric_sent_latency_inflation, -1.0, 10.0),
    SenderMonitorIntervalMetric("conn min latency", _mi_metric_conn_min_latency, 0.0, 100.0),
    SenderMonitorIntervalMetric("latency increase", _mi_metric_latency_increase, 0.0, 100.0),
    SenderMonitorIntervalMetric("latency ratio", _mi_metric_latency_ratio, 1.0, 10000.0),
    SenderMonitorIntervalMetric("send ratio", _mi_metric_send_ratio, 0.0, 1000.0)
]


