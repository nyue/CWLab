import sys
import os
from flask import render_template, jsonify, redirect, flash, url_for, request, send_from_directory
# from flask_login import current_user, login_user, logout_user, login_required
from werkzeug.urls import url_parse
from . import app 
from .general_use import fetch_files_in_dir, is_allowed_file, allowed_extensions_by_type
import requests
from re import sub, match
from xls2cwl_job.web_interface import read_template_attributes as read_template_attributes_from_xls
from xls2cwl_job.web_interface import get_param_config_info as get_param_config_info_from_xls
from xls2cwl_job.web_interface import gen_form_sheet
from xls2cwl_job import only_validate_xls, transcode as make_yaml_runs
from time import sleep
from shutil import move



@app.route('/get_job_templ_list/', methods=['GET','POST'])
def get_job_templ_list():   # returns list of job templates
                            # for already imported CWL documents
    messages = []
    templates = []
    try:
        # read list of template files:
        templates = fetch_files_in_dir(
            dir_path=app.config['CWL_DIR'], 
            file_exts=[".xlsx"],
            search_string=".job_templ",
            ignore_subdirs=True
        )
        # add field for cwl_target
        for i, t  in enumerate(templates):
            templates[i]["cwl_target"] = sub(r'\.job_templ$', '', t["file_nameroot"])
    except SystemExit as e:
        messages.append( { 
            "type":"error", 
            "text": str(e) 
        } )
    except:
        messages.append( { 
            "type":"error", 
            "text":"An uknown error occured reading job templates for the imported CWL documents." 
        } )
    return jsonify({
            "data": templates,
            "messages": messages
        }
    )


@app.route('/get_job_templ_config_info/', methods=['POST'])
def get_job_templ_config_info():    # returns all parmeter and its default mode (global/job specific) 
                                    # for a given xls config
    cwl_target = request.get_json()["cwl_target"]
    job_templ_filepath = os.path.join(app.config['CWL_DIR'], cwl_target + ".job_templ.xlsx")
    messages = []
    param_config_info = []
    template_attributes = []
    try:
        param_config_info = get_param_config_info_from_xls(job_templ_filepath)
        template_attributes = read_template_attributes_from_xls(job_templ_filepath)
    except SystemExit as e:
        messages.append( { 
            "type":"error", 
            "text": str(e) 
        } )
    except:
        messages.append( { 
            "type":"error", 
            "text":"An uknown error occured reading the job template config." 
        } )
    return jsonify({
        "data":{
            "params":param_config_info,
            "templ_attr": template_attributes
        },
        "messages":messages
    })


@app.route('/generate_param_form_sheet/', methods=['POST'])
def generate_param_form_sheet():    # generate param form sheet with data sent
                                    # by the client
    messages = []
    data = {}
    request_json = request.get_json()
    try:
        filename = str(request_json["job_id"]) + ".input." + str(request_json["sheet_format"])
        gen_form_sheet(
            output_file_path = os.path.join(
                app.config["TEMP_DIR"],
                filename
            ),
            template_config_file_path = os.path.join(
                app.config['CWL_DIR'], 
                request_json["cwl_target"] + ".job_templ.xlsx"
            ),
            has_multiple_runs= request_json["run_mode"],
            run_names=request_json["run_names"],
            param_is_run_specific=request_json["param_modes"],
            show_please_fill=True
        )
        data["get_form_sheet_href"] = url_for("get_param_form_sheet", form_sheet_filename=filename)
        print(data)
    except SystemExit as e:
        messages.append( { 
            "type":"error", 
            "text": str(e) 
        } )
    except:
        messages.append( { 
            "type":"error", 
            "text":"An uknown error occured reading the job template config." 
        } )
    return jsonify({
        "data":data,
        "messages":messages
    })


@app.route('/get_param_form_sheet/<form_sheet_filename>', methods=['GET','POST'])
def get_param_form_sheet(form_sheet_filename):
    if(
        match(r'.*\.input\.(' + "|".join(allowed_extensions_by_type["spreadsheet"]) + r')$', 
        form_sheet_filename)
    ):
        return send_from_directory(
            os.path.abspath(app.config["TEMP_DIR"]),
            form_sheet_filename,
            attachment_filename=form_sheet_filename, 
            as_attachment=True
        )
    else:
        return "invalide request"


@app.route('/send_filled_param_form_sheet/', methods=['POST'])
def send_filled_param_form_sheet():
    messages = []
    data = []
    try:
        if 'file' not in request.files:
            sys.exit( 'No file received.')

        import_file = request.files['file']

        if import_file.filename == '':
            sys.exit( "No file specified.")

        if not is_allowed_file(import_file.filename, type="spreadsheet"):
            sys.exit( "Wrong file type. Only files with following extensions are allowed: " + 
                ", ".join(allowed_extensions_by_type["spreadsheet"]))
        import_fileext = os.path.splitext(import_file.filename)[1].strip(".").lower()
        
        # save the file to the CWL directory:
        job_id = str(request.form.get("meta")).strip("\"") # ignore non ascii characters
        import_filename = job_id + ".input." + import_fileext
        import_filepath = os.path.join(app.config['TEMP_DIR'], import_filename)
        import_file.save(import_filepath)
    except SystemExit as e:
        messages.append( { "type":"error", "text": str(e) } )
    except:
        messages.append( { 
            "type":"error", 
            "text":"An uknown error occured while uploading." 
        } )
    
    if len(messages) == 0:
        try:
            # validate the uploaded form sheet:
            validation_result = only_validate_xls(
                sheet_file=import_filepath,
                validate_paths=False, search_paths=False, search_subdirs=False, input_dir=""
            )
            if validation_result != "VALID":
                os.remove(import_filepath)
                sys.exit(validation_result)
        except SystemExit as e:
            messages.append( { "type":"error", "text": "The provided form failed validation: " + str(e) } )
        except:
            messages.append( { 
                "type":"error", 
                "text":"An uknown error occured while validating the form sheet." 
            } )

    if len(messages) == 0:
        messages.append( { 
            "type":"success", 
            "text": "The filled form was successfully imported and validated."
        } )
    
    return jsonify({"data":data,"messages":messages})


@app.route('/create_job/', methods=['POST'])
def create_job():    # generate param form sheet with data sent
                                    # by the client
    messages = []
    data = {}
    request_json = request.get_json()
    try:
        job_id = str(request_json["job_id"])
        sheet_form = job_id + ".input." + str(request_json["sheet_format"])
        sheet_form_path = os.path.join(app.config['TEMP_DIR'], sheet_form)

        if not os.path.isfile(sheet_form_path):
            sys.exit("Could not find the filled parameter sheet \"" + sheet_form + "\".")
        
        if not is_allowed_file(sheet_form, type="spreadsheet"):
            sys.exit( "The filled parameter sheet \"" + sheet_form + "\" has the wrong file type. " +
                "Only files with following extensions are allowed: " + 
                ", ".join(allowed_extensions_by_type["spreadsheet"]))

        # prepare job directory
        job_dir = os.path.join(app.config["EXEC_DIR"], job_id)
        os.mkdir(job_dir)

        # Move form sheet to job dir:
        sheet_form_dest_path = os.path.join(job_dir, sheet_form)
        move(sheet_form_path, sheet_form_dest_path)
        
        # create yaml runs:
        make_yaml_runs(
            sheet_file=sheet_form_dest_path,
            output_basename="run",
            output_suffix=".yaml",
            output_dir=job_dir,
            validate_paths=False, search_paths=False, search_subdirs=False, input_dir=""
        )

        messages.append( { 
            "type":"success", 
            "text":"Successfully created job \"" + job_id + "\"." 
        } )
    except SystemExit as e:
        messages.append( { 
            "type":"error", 
            "text": str(e) 
        } )
    except:
        messages.append( { 
            "type":"error", 
            "text":"An uknown error occured." 
        } )
    return jsonify({
        "data":data,
        "messages":messages
    })