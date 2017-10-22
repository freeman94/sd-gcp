#!/bin/bash

export JOB_NAME="train_$(date +%Y%m%d_%H%M%S)"
export BUCKET_NAME=lstm-training
export JOB_DIR=gs://${BUCKET_NAME}
export OUTPUT_PATH=gs://${BUCKET_NAME}/${JOB_NAME}
export REGION=us-east1

gcloud ml-engine jobs submit training "${JOB_NAME}" \
	--job-dir "gs://${BUCKET_NAME}" \
	--runtime-version 1.2 \
	--module-name trainer.train \
	--package-path ./trainer \
	--region "${REGION}" \
	--config=trainer/cloudml-gpu.yaml \
	-- \
	--job_type "cloud" \
	--job_dir "${JOB_DIR}" \
	--job_name "${JOB_NAME}" \
	--data_dir "kinetics-10" \
	--split 0.66 \
	--seed 137 \
	--seq_length 10 \
	--batch_size 32 \
	--model_structure "gru" \
	--recurrent_dropout 0.0 \
	--unit_forget_bias "False"
