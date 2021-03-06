#!/usr/bin/python3
#########################################################
#
#   > Parser / Evaluator for CI files
#   > in GitLab CI file like syntax
#
# vim: sw=4 ts=4 noexpandtab:

import os, sys, threading
import yaml, re
from copy import deepcopy

#########################################################
## List of keys
# See for comparision and more info: https://docs.gitlab.com/ce/ci/yaml/README.html
#
PIPELINEKEY_SERVICES = "services"             # Will probably never get supported
PIPELINEKEY_IMAGE = "image"                   # Docker / Image manager info
PIPELINEKEY_VARIABLES = "variables"           # Job global env variables
PIPELINEKEY_STAGES = "stages"                 # job order
PIPELINEKEY_BEFORE_SCRIPT = "before_script"   # Script to run before all jobs
PIPELINEKEY_AFTER_SCRIPT = "after_script"     # Script to run after all jobs

JOBKEY_VARIABLES = PIPELINEKEY_VARIABLES      # Exported env vars
JOBKEY_ONLY = "only"                          # Only start job if matched
JOBKEY_TAGS = "tags"                          # Only start job on client with given tag
JOBKEY_STAGE = "stage"                        # The stage (build,text,..)
JOBKEY_SCRIPT = "script"                      # List of actions to execute
JOBKEY_ARTIFACTS = "artifacts"                # List of things to place in job artifact archive
JOBKEY_DEPENDENCIES = "dependencies"          # Restores artifacts from given jobs
JOBKEY_ARTIFACTS_NAME = "name"
JOBKEY_ARTIFACTS_PATHS = "paths"
JOBKEY_ARTIFACTS_EXPIRE_IN = "expire_in"
JOBKEY_ONLY_VARIABLES = "variables"
JOBKEY_ONLY_REFS = "refs"

MATRIXKEY_STAGES = PIPELINEKEY_STAGES         # available stages
MATRIXKEY_IMAGE = PIPELINEKEY_IMAGE           # image to load for this pipeline
MATRIXKEY_JOBS = "jobs"                       # collection of jobs, grouped by index of stage

# Reserved keys - list of disallowed job names
PIPELINES_KEYS = [PIPELINEKEY_STAGES, PIPELINEKEY_IMAGE, PIPELINEKEY_BEFORE_SCRIPT, PIPELINEKEY_AFTER_SCRIPT, PIPELINEKEY_VARIABLES, PIPELINEKEY_SERVICES]
JOB_KEYS = [JOBKEY_DEPENDENCIES,JOBKEY_SCRIPT,JOBKEY_STAGE,JOBKEY_VARIABLES,JOBKEY_ARTIFACTS]


class IdGenerator(object):
  """
  A class which provides an ID for jobs & pipelines.
  Supposed to be passed as per-project preserved object.
  """

  def __init__(self, last_job_id=0, last_pipeline_id=0):
    self._last_job_id = last_job_id
    self._last_pipeline_id = last_pipeline_id
    self._lock = threading.Lock()

  def next_pipeline_id(self):
    with self._lock:
      self._last_pipeline_id += 1
      ret = self._last_pipeline_id
    return ret
  def next_job_id(self):
    with self._lock:
      self._last_job_id += 1
      ret = self._last_job_id
    return ret
  def current_ids(self):
    return {"job": self._last_job_id, "pipeline": self._last_pipeline_id}


class CiFile:
  """
  1) Init this with a per-project preservered id_generator with initial values (e.g. from DB)
     Optionally pass variables (from e.g. API) in the variables dict parameter
  2) Use `read_cifile` to read a cifile from YAML or pass a otherwise read dict
     This will apply many fixes, refactorings and other actions to make it ready for use
  3) Use `prepare_run_with` when you have repository details available and you are about to start building
     There are some important required arguments which are needed to filter jobs not allowed to run (by e.g. variable condition)
  4) Use `convert_to_matrix` to get a copy of the values in a |pipeline -> stages -> jobs| hierachy
     This allows to easy process the jobs in correct order
  5) Processing order inside a stage does not matter, can be random
     All jobs in one stage must have run and all must be successful for the next stage to start
     When all jobs in last stage are completed successfully, the pipeline is successfully
  """

  def __init__(self,
               id_generator=IdGenerator(),
               default_stage="test",
               default_image="local_system_shell",
               default_artifact_expire_in="3 days",
               default_artifact_name="${CI_JOB_NAME}_${CI_COMMIT_REF_NAME}_${CI_JOB_ID}",
               variables=None):
    """
    Provide a project-wide id generator, initialized with the last ID used.
    """

    self._cidict = {}
    self._default_stage = default_stage
    self._default_image = default_image
    self._default_artifact_name = default_artifact_name
    self._default_artifact_expire_in = default_artifact_expire_in
    self._forced_variables = variables or {}
    self._id_generator = id_generator

  def convert_to_matrix(self):
    """
    Convert CI Dictionary to a matrix view. Note that this is a copy of an
    internal dict and changes don't affect the internal dict nor the matrix
    can be used as CiFile again.

    Matrix:
    - image
    - jobs: Dict of jobs grouped by stage, orded by order in `stages`
    - stages: Name and order of all stages
    """

    pipeline = {
      MATRIXKEY_JOBS: [],
      MATRIXKEY_STAGES: self._cidict[PIPELINEKEY_STAGES],
      MATRIXKEY_IMAGE: self._cidict[PIPELINEKEY_IMAGE],
    }
    for stage in self._cidict[PIPELINEKEY_STAGES]:
      jobs =  {}
      for jobname, job in self._cidict.items():
        if jobname not in PIPELINES_KEYS and isinstance(job, dict) and job[JOBKEY_STAGE] == stage:
          jobs[jobname] = job
      pipeline[MATRIXKEY_JOBS].append(jobs)
    return pipeline

  @staticmethod
  def print_matrix(matrix):
    """
    Print given matrix (from `convert_to_matrix`) to command line.
    """

    for i, stage in enumerate(matrix[MATRIXKEY_STAGES]):
      print("\nJobs for " + stage + " (" +  str(len(matrix[MATRIXKEY_JOBS][i])) +"):")
      for jobname, job in matrix[MATRIXKEY_JOBS][i].items():
        print("├──" + str(jobname)) #+ ", " + job[JOBKEY_VARIABLES]["CI_JOB_ID"])

  def _parse_cidict(self, cidict):
    """
    Fill missing but deductable information into the *cidict* and store it
    in self.
    """

    if not isinstance(cidict, dict):
      raise TypeError('expected dict, got {}'.format(type(cidict).__name__))

    self._cidict = cidict

    # Note: calls are order dependent.

    self._pipeline_various_tweaks()

    self._job_scan_apply_stages()
    self._job_convert_list_to_script()
    self._job_scan_apply_stages()

    self._job_convert_before_after_script()

    self._job_various_tweaks()
    self._job_script_always_list()
    self._job_combine_variables()
    self._job_streamline_only()
    self._job_streamline_artifacts()

    return self

  def prepare_run_with(self, ci_commit_ref_name, ci_pipeline_source, ci_commit_message, ci_commit_sha, ci_commit_tag=None):
    """
    Based upon          https://docs.gitlab.com/ce/ci/variables/#predefined-variables-environment-variables
    ci_commit_ref_name: Branch or tag name for which project is built
    ci_pipeline_source: Indicates how the pipeline was triggered. Options include push, web, trigger, schedule, api, pipeline
    ci_commit_message:  Full commit message
    ci_commit_sha:      Commit hash
    ci_commit_tag:      The commit tag name. Present only when building tags.
    """

    self._pipeline_add_buildinfo(ci_commit_ref_name, ci_pipeline_source, ci_commit_message, ci_commit_sha, ci_commit_tag)
    self._evaluate_jobs()
    self._assign_ids()

  def read_cifile(self, fyaml=None, dictionary=None):
    """
    Read and parse CI file from yaml or otherwise loaded dictionary. Returns
    *self* for call chaining.
    """

    if fyaml:
      with open(fyaml, 'r') as f:
        dictionary=yaml.load(f.read())
    if dictionary:
      self._parse_cidict(dictionary)
    return self

  def get_cidict(self):
    """
    Get a copy of the internal cidict.
    """

    return deepcopy(self._cidict)

  @staticmethod
  def extract_str_list(obj, noneval=[]):
    """
    Try to get a list of strings from *obj*, allowing it to exist in various
    formats.
    """

    if obj is None:
      return noneval
    if isinstance(obj, str):
      return [obj]
    if isinstance(obj, (int, bool, float)):
      return [str(obj)]
    elif isinstance(obj, list):
      return [str(o) for o in obj if isinstance(o, (str,int, bool, float))]
    return noneval

  @staticmethod
  def extract_str(obj, noneval=""):
    """
    Try to get a string from *obj*, taking various cases into account.
    """

    if isinstance(obj, (str,int, bool, float)):
      return str(obj)
    elif isinstance(obj, list):
      strs = [ o for o in obj if isinstance(o,str) ]
      return strs[0] if strs else noneval
    return noneval

  @staticmethod
  def extract_str_dict(d):
    """
    Apply #extract_str() to every value in the dictionary *d*.
    Returns a new dictionary that does not contain the values that could
    not be coerced to strings.
    """

    ret = {}
    for var, value in (d if isinstance(d, dict) else {}).items():
      value = CiFile.extract_str(value, None)
      if value:
        ret[var] = value
    return ret

  def _pipeline_various_tweaks(self):
    """
    Various smaller tweaks for the whole pipeline.
    """

    # filter out templates (started with dot)
    self._cidict = {jobname:job for (jobname,job) in self._cidict.items() if isinstance(jobname,str) and not jobname.startswith(".") }

    # image (docker etc) - ensure one string
    tmp = self.extract_str(self._cidict[PIPELINEKEY_IMAGE] if PIPELINEKEY_IMAGE in self._cidict else self._default_image, self._default_image)
    self._cidict[PIPELINEKEY_IMAGE] = tmp if isinstance(tmp, str) else self._default_image

  def _job_scan_apply_stages(self):
    """
    Scan for stages, assign default stages to jobs not having one.
    """

    # Extract stages list - string only list without subitems
    stages = self._cidict.pop(PIPELINEKEY_STAGES) if PIPELINEKEY_STAGES in self._cidict else []

    # Scan for unspecified stages
    for job, v in self._cidict.items():
      if job not in PIPELINES_KEYS and isinstance(v, dict) and JOBKEY_STAGE in self._cidict[job]: # is job with stage
        stage = self.extract_str(self._cidict[job][JOBKEY_STAGE])
        if stage and not stage in stages:
          stages.append(stage)

    # Add default stage when nothing found
    stages=stages if stages else [self._default_stage]

    # Assign default stage for jobs with none or invalid
    for jobname, v in self._cidict.items():
      if jobname not in PIPELINES_KEYS and isinstance(v, dict):
        stage=self.extract_str(self._cidict[jobname][JOBKEY_STAGE] if JOBKEY_STAGE in self._cidict[jobname] else stages[0], noneval=stages[0])
        self._cidict[jobname][JOBKEY_STAGE] = stage

    self._cidict[PIPELINEKEY_STAGES] = stages

  def _job_convert_before_after_script(self):
    """
    Convert before_script, post_script to a normal job (so this works like
    any other job in matrix ;)
    """

    for jobkey in [PIPELINEKEY_BEFORE_SCRIPT, PIPELINEKEY_AFTER_SCRIPT]:
      job = {JOBKEY_STAGE: jobkey, JOBKEY_SCRIPT: []}
      job[JOBKEY_SCRIPT] = self._cidict[jobkey] if jobkey in self._cidict else []
      if len(job[JOBKEY_SCRIPT]) > 0:
        del self._cidict[jobkey]
        self._cidict[jobkey.replace('_',':')] = job
        if jobkey == PIPELINEKEY_BEFORE_SCRIPT:
          self._cidict[PIPELINEKEY_STAGES].insert(0, PIPELINEKEY_BEFORE_SCRIPT)
        if jobkey == PIPELINEKEY_AFTER_SCRIPT:
          self._cidict[PIPELINEKEY_STAGES].append(PIPELINEKEY_AFTER_SCRIPT)

  def _job_convert_list_to_script(self):
    """
    If a job only has a list, move that to script
    job:name:                 job:name:
      - make                    script:
                                  - make
    """

    for jobname, v in self._cidict.items():
      if jobname not in PIPELINES_KEYS and isinstance(v, list):
        script=self.extract_str_list(self._cidict[jobname], noneval=None)
        if script:
          self._cidict[jobname] = {JOBKEY_SCRIPT: script}

  def _job_script_always_list(self):
    """
    Make sure a job script part is always a list and a job always has script
    job:name:                 job:name:
      script: make              script:
                                  - make
    """

    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS:
        if not JOBKEY_SCRIPT in job:
          job[JOBKEY_SCRIPT] = []
        script=self.extract_str_list(job[JOBKEY_SCRIPT], noneval=[])
        if script:
          self._cidict[jobname][JOBKEY_SCRIPT] = script

  def _job_streamline_artifacts(self):
    """
    Make sure a job always specifies artifact info accordingly
    job:name:
      artifacts:
        name: "name_of_archive_file"
        expire_in: "7 days"
        paths:
          - dist
          - build/some.log
    """

    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS:
        artifacts = {
          JOBKEY_ARTIFACTS_NAME: self._default_artifact_name,
          JOBKEY_ARTIFACTS_EXPIRE_IN: self._default_artifact_expire_in,
          JOBKEY_ARTIFACTS_PATHS: []
        }
        if JOBKEY_ARTIFACTS in job:
          if isinstance(job[JOBKEY_ARTIFACTS], list):
            artifacts[JOBKEY_ARTIFACTS_PATHS] = self.extract_str_list(job[JOBKEY_ARTIFACTS])
          if JOBKEY_ARTIFACTS_PATHS in job[JOBKEY_ARTIFACTS]:
            artifacts[JOBKEY_ARTIFACTS_PATHS] = self.extract_str_list(job[JOBKEY_ARTIFACTS][JOBKEY_ARTIFACTS_PATHS])
          artifacts[JOBKEY_ARTIFACTS_NAME] = job[JOBKEY_ARTIFACTS][JOBKEY_ARTIFACTS_NAME] if JOBKEY_ARTIFACTS_NAME in job[JOBKEY_ARTIFACTS] else self._default_artifact_name
          artifacts[JOBKEY_ARTIFACTS_EXPIRE_IN] = job[JOBKEY_ARTIFACTS][JOBKEY_ARTIFACTS_NAME] if JOBKEY_ARTIFACTS_EXPIRE_IN in job[JOBKEY_ARTIFACTS] else self._default_artifact_expire_in
        job[JOBKEY_ARTIFACTS] = artifacts

  def _job_streamline_only(self):
    """
    Make sure a job only part is always only/refs and only/variables
    job:name:
      only:
        refs:
          - triggers
        variables:
          - $BUILD_COOL_FEATURE
    """

    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS:
        if not JOBKEY_ONLY in job:
          job[JOBKEY_ONLY] = {}

        # Find values
        found = []
        tmp=self.extract_str_list(job[JOBKEY_ONLY], noneval=[])
        found.extend(tmp)
        if JOBKEY_ONLY_VARIABLES in job[JOBKEY_ONLY]:
          found.extend(self.extract_str_list(job[JOBKEY_ONLY][JOBKEY_ONLY_VARIABLES], noneval=[]))
        if JOBKEY_ONLY_REFS in job[JOBKEY_ONLY]:
          found.extend(self.extract_str_list(job[JOBKEY_ONLY][JOBKEY_ONLY_REFS], noneval=[]))

        # Remove some parts
        while JOBKEY_ONLY_REFS in found: found.remove(JOBKEY_ONLY_REFS)
        while JOBKEY_ONLY_VARIABLES in found: found.remove(JOBKEY_ONLY_VARIABLES)
        job[JOBKEY_ONLY] = {
          JOBKEY_ONLY_REFS: [ v for v in found if not v.startswith("$") ],
          JOBKEY_ONLY_VARIABLES: [ v for v in found if v.startswith("$") ],
        }

  def _job_various_tweaks(self):
    """
    Various small tweaks
    """

    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS:
        # job: Make sure "tags" is a list
        tmp = job[JOBKEY_TAGS] if JOBKEY_TAGS in job else []
        job[JOBKEY_TAGS] = self.extract_str_list(tmp, noneval=[])

        # job: Make sure "dependencies" is a list
        tmp = job[JOBKEY_DEPENDENCIES] if JOBKEY_DEPENDENCIES in job else []
        job[JOBKEY_DEPENDENCIES] = self.extract_str_list(tmp, noneval=[])

  def _job_combine_variables(self, injectdict={}):
    """
    Distribute variables from pipelines, ci and injected variables and ensure
    all variables sections are (k,v):(str,str) only.
    """

    # Extract and fix pipeline vars (lowest priority)
    pvars=self.extract_str_dict(self._cidict[PIPELINEKEY_VARIABLES] if PIPELINEKEY_VARIABLES in self._cidict else {})
    self._cidict[PIPELINEKEY_VARIABLES] = pvars

    # Distribute global variables to jobs and possible fix job vars
    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS and isinstance(job, dict):
        jvars=self.extract_str_dict(job[JOBKEY_VARIABLES] if JOBKEY_VARIABLES in job else {})

        # pipeline vars
        for pvar, gvalue in pvars.items():
          if not pvar in jvars:
            jvars[pvar]=gvalue

        # vars submitted by CI service - e.g. api trigger variables
        for civar, civalue in (self.extract_str_dict(self._forced_variables)).items():
          jvars[civar]=civalue

        # Additional injected vars
        for varname, varvalue in injectdict.items():
          jvars[varname]=varvalue

        self._cidict[jobname][JOBKEY_VARIABLES] = jvars

      # Remove pipeline variables dict so it won't get added again if this gets recalled
    self._cidict.pop(PIPELINEKEY_VARIABLES)

  def _pipeline_add_buildinfo(self, CI_COMMIT_REF_NAME, CI_PIPELINE_SOURCE, CI_COMMIT_MESSAGE, CI_COMMIT_SHA, CI_COMMIT_TAG=None):
    """
    Distribute information about the repository and what to build from it
    See `prepare` (args forwarded) and GL docs for parameter info
    """

    pd = {
      # Mark that job is executed in CI environment
      "CI": True,
      # Branch or tag name for which project is built
      "CI_COMMIT_REF_NAME": CI_COMMIT_REF_NAME,
      # Indicates how the pipeline was triggered. Options include push, web, trigger, schedule, api, pipeline
      "CI_PIPELINE_SOURCE": CI_PIPELINE_SOURCE,
      # Full commit message
      "CI_COMMIT_MESSAGE": CI_COMMIT_MESSAGE,
      #  Commit hash
      "CI_COMMIT_SHA": CI_COMMIT_SHA,
      # CI_COMMIT_REF_NAME lowercased, shortened to 63 bytes, and with everything except 0-9 and a-z replaced with -. No leading / trailing -
      "CI_COMMIT_REF_SLUG": re.sub('[^0-9a-zA-Z]+', '-', CI_COMMIT_REF_NAME.lower())[:63].strip('-'),
      # The title of the commit - the full first line of the message
      "CI_COMMIT_TITLE":  CI_COMMIT_MESSAGE.split('\n')[0],
      # description of the commit: the message without first line, if the title is shorter than 100 characters; full message in other case.
      "CI_COMMIT_DESCRIPTION": CI_COMMIT_MESSAGE if len(CI_COMMIT_MESSAGE) < 100 else re.sub(r'^[^\n]*\n', '', CI_COMMIT_MESSAGE),
      # flag to indicate that job was triggered  (Doesn't include e.g. push events)
      "CI_PIPELINE_TRIGGERED": True if CI_PIPELINE_SOURCE in ["trigger", "api", "pipeline", "schedule", "web"] else False
    }
    if CI_COMMIT_TAG:
      pd["CI_COMMIT_TAG"] = CI_COMMIT_TAG
    self._job_combine_variables(pd)

    # Add job variables
    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS and isinstance(job, dict):
        jvars=self.extract_str_dict(job[JOBKEY_VARIABLES] if JOBKEY_VARIABLES in job else {})
        jvars["CI_JOB_NAME"] = jobname
        jvars["CI_JOB_STAGE"] = job[JOBKEY_STAGE]
        self._cidict[jobname][JOBKEY_VARIABLES] = jvars

  def _evaluate_jobs(self):
    """
    Removes all jobs that are not supposed to run
    This does evaluation of variables in |only:| section and removes all jobs whos dependencies are not matched
    """

    bak_env = os.environ
    jobs_to_remove = []
    for jobname, job in self._cidict.items():
      if jobname not in PIPELINES_KEYS and isinstance(job, dict):
        os.environ = bak_env
        job_run_script = False
        job_run_ref_ok = False
        job_run_ref_variables = len(job[JOBKEY_ONLY][JOBKEY_ONLY_VARIABLES]) == 0

        for jvar, jvalue in job[JOBKEY_VARIABLES].items():
          os.environ[jvar]=jvalue

        if JOBKEY_SCRIPT in job and job[JOBKEY_SCRIPT]:
          job_run_script = True
        elif JOBKEY_DEPENDENCIES in job and job[JOBKEY_DEPENDENCIES] and JOBKEY_ARTIFACTS in job and job[JOBKEY_ARTIFACTS]:
          job_run_script = True

        # No restrictions given
        if not job[JOBKEY_ONLY][JOBKEY_ONLY_REFS] and not job[JOBKEY_ONLY][JOBKEY_ONLY_VARIABLES]:
          job_run_ref_ok = True
        # Only run for triggers
        if "triggers" in job[JOBKEY_ONLY][JOBKEY_ONLY_REFS] and job[JOBKEY_VARIABLES]["CI_PIPELINE_TRIGGERED"]:
          job_run_ref_ok = True
        # Only run for commits having a tag
        if "tags" in job[JOBKEY_ONLY][JOBKEY_ONLY_REFS] and "CI_COMMIT_TAG" in job[JOBKEY_VARIABLES]:
          job_run_ref_ok = True
        # Only run for commits having a tag
        for ref in job[JOBKEY_ONLY][JOBKEY_ONLY_REFS]:
          if ref not in ["tags","triggers"] and job[JOBKEY_VARIABLES]["CI_COMMIT_REF_NAME"] == ref:
            job_run_ref_ok = True

        # Evaluate all in variables
        for condition in job[JOBKEY_ONLY][JOBKEY_ONLY_VARIABLES]:
          split = condition.split("==")
          left = os.path.expandvars(split[0].strip().strip('"'))
          right = os.path.expandvars(split[1]).strip().strip('"') if len(split) > 1 else None

          # For checking compared values
          #print(str(left) + "<--->" + str(right))

          # 5 Variable presence check
          # - $VARIABLE
          if left and right is None:
            job_run_ref_variables = True
          # 3 Checking for an empty variable
          # - $VARIABLE == ""
          elif left and right == "":
            job_run_ref_variables = True
          # 2 Checking for an undefined value
          # - $VARIABLE == ""
          elif "$" in left and right and right in ["null","nil","None"]:
            job_run_ref_variables = True
          # 1,4 Equality matching using a string
          # - $VARIABLE == "Text"
          # - $VARIABLE == $VARIABLE2
          elif right and left == right:
            job_run_ref_variables = True

        job_run = job_run_ref_ok and job_run_ref_variables and job_run_script
        if not job_run:
          jobs_to_remove.append(jobname)

    self._cidict = {jobname:job for (jobname,job) in self._cidict.items() if isinstance(jobname,str) and not jobname in jobs_to_remove }
    removedsomething = True
    while removedsomething:
      removedsomething = False
      jobnames = self._cidict.keys()
      for jobname, job in self._cidict.items():
        if jobname not in PIPELINES_KEYS and isinstance(job, dict):
          for dep in job[JOBKEY_DEPENDENCIES]:
            if not dep in jobnames:
              removedsomething = jobname
              break
      if removedsomething:
            self._cidict = {jobname:job for (jobname,job) in self._cidict.items() if isinstance(jobname,str) and not jobname == removedsomething }
    os.environ = bak_env

  def _assign_ids(self):
    """
    Assigns pipeline and job ids
    """

    pipeline_id = str(self._id_generator.next_pipeline_id())

    # Add job-ids on stage basis, temporarly convert to matrix view for this
    matrix = self.convert_to_matrix()
    for i, stage in enumerate(matrix[MATRIXKEY_STAGES]):
      for jobname, job in matrix[MATRIXKEY_JOBS][i].items():
        jvars=self.extract_str_dict(job[JOBKEY_VARIABLES] if JOBKEY_VARIABLES in job else {})
        jvars["CI_PIPELINE_ID"] = pipeline_id
        jvars["CI_JOB_ID"] = str(self._id_generator.next_job_id())
        self._cidict[jobname][JOBKEY_VARIABLES] = jvars


def get_argument_parser(prog=None):
  import argparse
  parser = argparse.ArgumentParser(prog=prog)
  parser.add_argument('cifile', nargs='?', default='.ci.yml')
  parser.add_argument('--branch', default='master', help='The brach name for which the pipeline was triggered.')
  parser.add_argument('--source', default='trigger', help='Indicates how the pipeline was triggered.')
  parser.add_argument('--ref', default='0'*32, help='The commit ref for which the pipeline was triggered. Defaults to empty SHA.')
  parser.add_argument('--msg', default='No message.', help='Full commit message.')
  parser.add_argument('--tag', help='Commit tag.')
  parser.add_argument('--dump', action='store_true', help='Dump the completed CI configuration.')
  parser.add_argument('--dump-matrix', action='store_true', help='Dump the build matrix.')
  parser.add_argument('--dump-run', action='store_true', help='Dump how a run of the CI file would look like.')
  parser.add_argument('--env', default=[], action='append', help='Define an environment variable.')
  return parser


def main(argv=None, prog=None):
  parser = get_argument_parser(prog)
  args = parser.parse_args(argv)

  env = {}
  for item in args.env:
    key, value = item.partition('=')[::2]
    env[key] = value

  cifile = CiFile(variables=env)
  cifile.read_cifile(args.cifile)
  cifile.prepare_run_with(args.branch, args.source, args.msg, args.ref, args.tag)
  matrix = cifile.convert_to_matrix()

  if args.dump:
    print(yaml.dump(matrix))
    return 0

  if args.dump_matrix:
    cifile.print_matrix(matrix)
    return 0

  if args.dump_run:
    for i, stagename in enumerate(matrix[MATRIXKEY_STAGES]):
      stage = matrix[MATRIXKEY_JOBS][i]
      print('Stage: {}'.format(stagename))
      for jobname, job in stage.items():
        print('  Job: {}'.format(jobname))
        for cmd in job[JOBKEY_SCRIPT]:
          print('    {}'.format(cmd))
    return 0


if __name__ == '__main__':
  sys.exit(main())
