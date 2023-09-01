
import os
import time
import importlib
import matplotlib.pyplot as plt
import numpy as np
import torch
import json

from tqdm import tqdm
from glob import glob
from torch import nn
from typing import Optional, Any, Callable

from src.clustering import Clustering
from src.dataset import time_slice_collator, SPDTimesliceTracksDataset, DatasetMode
from torch.utils.data import DataLoader
from src.visualization import draw_embeddings
from src.metrics import *
from src.clustering import Clustering


BATCH_SIZE = 1


class ModelEvaluator:
    def __init__(
            self,
            model_dir: str,
            model: nn.Module,
            clustering_model: Optional[Any] = None,
            test_samples: int = 100,
            detector_efficiency: float = 1.0,
            hits_normalizer: Optional[Callable] = None,
            num_workers: int = 0,
            pin_memory: bool = False,
            device: str = "cpu"):

        self.model_dir = model_dir
        self.device = device
        self.model = self._initialize_model(model)
        self.clustering = Clustering(clustering_model)
        self.test_data = SPDTimesliceTracksDataset(
            n_samples=test_samples,
            detector_eff=detector_efficiency,
            hits_normalizer=hits_normalizer,
            mode=DatasetMode.test
        )
        self.test_loader = DataLoader(
            self.test_data,
            batch_size=BATCH_SIZE,
            shuffle=False,
            collate_fn=time_slice_collator,
            num_workers=num_workers,
            pin_memory=pin_memory
        )
        self.metrics = [
            SilhouetteScoreMetric(),
            DaviesBouldinScoreMetric(),
            CalinskiHarabaszScoreMetric(),
            AccuracyScoreMetric(),
            F1ScoreMetric(),
            PrecisionScoreMetric(),
            RecallScoreMetric()
        ]


    def _initialize_model(self, model):
        checkpoint_path = glob(os.path.join(self.model_dir, "*.ckpt"))[0]
        model_checkpoint = torch.load(
            checkpoint_path, map_location=self.device)
        model_state_dict = {
            k.replace("model.", ""): v for k, v in model_checkpoint['state_dict'].items()}
        model.load_state_dict(model_state_dict)
        model.eval()
        return model


    def visualize_embeddings(self, num_samples=6):

        fig, axes = plt.subplots(2, 3, figsize=(15, 10))
        fig.suptitle(f'UMAP plots for the first {num_samples} samples')

        for i, batch in enumerate(self.data_loader):
            if i >= num_samples:
                break

            tracks, evt_ids = batch
            embeddings = self.model(tracks).detach().numpy()
            evt_ids_np = evt_ids.detach().numpy()

            ax = axes[i // 3, i % 3]
            plt.sca(ax)

            draw_embeddings(embeddings=embeddings,
                            labels=evt_ids_np, sample_idx=i, split_name='')

        plt.tight_layout()
        plt.subplots_adjust(top=0.90)
        plt.savefig(os.path.join(self.base_path,
                    self.run_name, "embeddings_subplot.png"))


    def evaluate(self, save_result=False):
        embedding_times = []
        clustering_times = []
        metrics_results = {}

        for batch in tqdm(self.test_loader):
            tracks, evt_ids = batch
            start_time = time.time()
            embeddings = self.model(tracks)
            end_time = time.time()
            embedding_times.append(end_time - start_time)

            emb_np = embeddings.detach().numpy()
            evt_ids_np = evt_ids.detach().numpy()
            start_time = time.time()
            cluster_preds = self.clustering.cluster_and_link(
                emb_np, evt_ids_np)
            end_time = time.time()
            clustering_times.append(end_time - start_time)

            for metric in self.metrics:
                if isinstance(metric, (
                    F1ScoreMetric,
                    PrecisionScoreMetric,
                    RecallScoreMetric,
                    AccuracyScoreMetric
                )):
                    metric.update(evt_ids_np, cluster_preds)
                elif isinstance(metric, BaseScoreMetric):
                    metric.update(emb_np, cluster_preds)

        avg_embedding_time = np.mean(embedding_times)
        std_embedding_time = np.std(embedding_times)
        avg_clustering_time = np.mean(clustering_times)
        std_clustering_time = np.std(clustering_times)

        for metric in self.metrics:
            metric_name = type(metric).__name__
            metric_val = metric.compute()
            metrics_results[metric_name] = metric_val

        results = {
            "time": {
                "embeddings": {"mean": avg_embedding_time, "std": std_embedding_time},
                "clustering": {"mean": avg_clustering_time, "std": std_clustering_time}
            },
            "metrics": metrics_results
        }

        if save_result:
            with open(os.path.join(self.model_dir, "eval_res.json"), "w") as f:
                json.dump(results, f)
            # TODO: add here visualize embeddings

        return results
