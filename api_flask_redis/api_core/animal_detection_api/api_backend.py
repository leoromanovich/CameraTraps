# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import os
import json
import time
import redis
import argparse

from datetime import datetime
from io import BytesIO

from requests_toolbelt.multipart.encoder import MultipartEncoder

import config
from run_tf_detector import TFDetector
import visualization.visualization_utils as viz_utils

db = redis.StrictRedis(host=config.REDIS_HOST, port=config.REDIS_PORT)
current_directory = os.path.dirname(os.path.realpath(__file__))

def detect_process():
     
     while True:
        
        serialized_entry = db.lpop(config.REDIS_QUEUE)        
        all_detection_results = []
        inference_time_detector = []
        
        if serialized_entry:
            
            entry = json.loads(serialized_entry)
            id = entry['id']
            return_confidence_threshold = entry['return_confidence_threshold']

            try:
                
                temp_direc = f'{current_directory}/{config.TEMP_FOLDER}/{id}'
                
                for filename in os.listdir(temp_direc):
                    
                    image = open(f'{temp_direc}/{filename}', "rb")
                    image = viz_utils.load_image(image)

                    start_time = time.time()
                    result = detector.generate_detections_one_image(image, filename)
                    all_detection_results.append(result)

                    elapsed = time.time() - start_time
                    inference_time_detector.append(elapsed)
                    
            except Exception as e:
                print('Error performing detection on the images: ' + str(e))
                
                db.set(entry['id'], json.dumps({ 
                    'status': 500,
                    'error': 'Error performing detection on the images: ' + str(e)
                }))

            # Filter the detections by the confidence threshold
            
            # json to return to the user along with the rendered images if they opted for it
            #
            # Each result is [ymin, xmin, ymax, xmax, confidence, category]
            detections = {}  
            
            try:
                
                for result in all_detection_results:
                    
                    image_name = result['file']
                    _detections = result.get('detections', None)
                    detections[image_name] = []

                    if _detections is None:
                        continue

                    for d in _detections:
                        if d['conf'] > return_confidence_threshold:
                            res = TFDetector.convert_to_tf_coords(d['bbox'])
                            res.append(d['conf'])
                            res.append(int(d['category']))  # category is an int here, not string as in the async API
                            detections[image_name].append(res)
    
                ## TODO: log
                db.set(entry['id'], json.dumps({ 
                    'status': 200,
                    'all_detection_results': all_detection_results,
                    'detections': detections,
                    'inference_time_detector': inference_time_detector
                }))
              
            except Exception as e:
                print('Error consolidating the detection boxes: ' + str(e))
                
                db.set(entry['id'], json.dumps({ 
                    'status': 500,
                    'error': 'Error consolidating the detection boxes:' + str(e)
                }))

    
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='api backend')

    # use --non-docker argument if you are testing without docker directly in terminal for debugging
    # python api_frontend.py --non-docker
    parser.add_argument('--non-docker', action="store_true", default=False)
    args = parser.parse_args()

    if args.non_docker:
        model_path = config.DETECTOR_MODEL_PATH_DEBUG
    else:
        model_path = config.DETECTOR_MODEL_PATH

    detector = TFDetector(model_path)
    detect_process() 
