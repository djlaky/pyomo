# === Required imports ===
import pyomo.environ as pyo
from pyomo.dae import ContinuousSet, DerivativeVar, Simulator

import itertools
import json
# ========================


def expand_model_components(m, base_components, index_sets):
    """
    Takes model components and index sets and returns the
    model component labels.

    Arguments
    ---------
    m: Pyomo model
    base_components: list of variables from model 'm'
    index_sets: list, same length as base_components, where each
                element is a list of index sets, or None
    """
    for val, indexes in itertools.zip_longest(base_components, index_sets):
        # If the variable has no index,
        # add just the model component
        if not val.is_indexed():
            yield val
        # If the component is indexed but no
        # index supplied, add all indices
        elif indexes is None:
            yield from val.values()
        else:
            for j in itertools.product(*indexes):
                yield val[j]


class Experiment(object):
    def __init__(self):
        self.model = None

    def get_labeled_model(self):
        raise NotImplementedError(
            "Derived experiment class failed to implement get_labeled_model"
        )


class ReactorExperiment(object):
    def __init__(self, data, nfe, ncp):
        self.data = data
        self.nfe = nfe
        self.ncp = ncp
        self.model = None

    def get_labeled_model(self):
        if self.model is None:
            self.create_model()
            self.finalize_model()
            self.label_experiment()
        return self.model

    def create_model(self):
        """
            This is an example user model provided to DoE library.
            It is a dynamic problem solved by Pyomo.DAE.

            Return
            ------
            m: a Pyomo.DAE model
        """

        m = self.model = pyo.ConcreteModel()

        # Model parameters
        m.R = pyo.Param(mutable=False, initialize=8.314)

        # Define model variables
        ########################
        # time
        m.t = ContinuousSet(bounds=[0, 1])

        # Concentrations
        m.CA = pyo.Var(m.t, within=pyo.NonNegativeReals)
        m.CB = pyo.Var(m.t, within=pyo.NonNegativeReals)
        m.CC = pyo.Var(m.t, within=pyo.NonNegativeReals)

        # Temperature
        m.T = pyo.Var(m.t, within=pyo.NonNegativeReals)

        # Arrhenius rate law equations
        m.A1 = pyo.Var(within=pyo.NonNegativeReals)
        m.E1 = pyo.Var(within=pyo.NonNegativeReals)
        m.A2 = pyo.Var(within=pyo.NonNegativeReals)
        m.E2 = pyo.Var(within=pyo.NonNegativeReals)

        # Differential variables (Conc.)
        m.dCAdt = DerivativeVar(m.CA, wrt=m.t)
        m.dCBdt = DerivativeVar(m.CB, wrt=m.t)

        ########################
        # End variable def.

        # Equation def'n
        ########################

        # Expression for rate constants
        @m.Expression(m.t)
        def k1(m, t):
            return m.A1 * pyo.exp(-m.E1 * 1000 / (m.R * m.T[t]))

        @m.Expression(m.t)
        def k2(m, t):
            return m.A2 * pyo.exp(-m.E2 * 1000 / (m.R * m.T[t]))

        # Concentration odes
        @m.Constraint(m.t)
        def CA_rxn_ode(m, t):
            return m.dCAdt[t] == -m.k1[t] * m.CA[t]

        @m.Constraint(m.t)
        def CB_rxn_ode(m, t):
            return m.dCBdt[t] == m.k1[t] * m.CA[t] - m.k2[t] * m.CB[t]

        # algebraic balance for concentration of C
        # Valid because the reaction system (A --> B --> C) is equimolar
        @m.Constraint(m.t)
        def CC_balance(m, t):
            return m.CA[0] == m.CA[t] + m.CB[t] + m.CC[t]

        ########################
        # End equation def'n

    def finalize_model(self):
        """
        Example finalize model function. There are two main tasks
        here:
            1. Extracting useful information for the model to align
               with the experiment. (Here: CA0, t_final, t_control)
            2. Discretizing the model subject to this information.

        Arguments
        ---------
        m: Pyomo model
        data: object containing vital experimental information
        nfe: number of finite elements
        ncp: number of collocation points for the finite elements
        """
        m = self.model

        # Unpacking data before simulation
        control_points = self.data['control_points']

        m.CA[0].fix(self.data['CA0'])
        m.CB[0].fix(self.data['CB0'])
        m.CC[0].fix(self.data['CC0'])
        m.t.update(self.data['t_range'])
        m.t.update(control_points)
        m.A1 = self.data['A1']
        m.A2 = self.data['A2']
        m.E1 = self.data['E1']
        m.E2 = self.data['E2']

        m.t_control = control_points

        # TODO: add simulation for initialization?????
        # Call the simulator (optional)

        # Discretizing the model
        discr = pyo.TransformationFactory("dae.collocation")
        discr.apply_to(m, nfe=self.nfe, ncp=self.ncp, wrt=m.t)

        # Initializing Temperature in the model
        cv = None
        for t in m.t:
            if t in control_points:
                cv = control_points[t]
            m.T[t] = cv

        # Unfixing initial temperature
        m.T[0.0].unfix()

        @m.Constraint(m.t - control_points)
        def T_control(m, t):
            """
            Piecewise constant Temperature between control points
            """
            neighbour_t = max(tc for tc in control_points if tc < t)
            return m.T[t] == m.T[neighbour_t]

    def label_experiment_impl(self, index_sets_meas):
        """
        Example for annotating (labeling) the model with a
        full experiment.

        Arguments
        ---------

        """
        m = self.model

        # Grab measurement labels
        base_comp_meas = [m.CA, m.CB, m.CC]
        m.experiment_outputs = pyo.Suffix(
            direction=pyo.Suffix.LOCAL,
        )
        m.experiment_outputs.update((k, None) for k in expand_model_components(m, base_comp_meas, index_sets_meas))

        # Grab design variables
        base_comp_des = [m.CA, m.T]
        index_sets_des = [[[m.t.first()]], [m.t_control]]
        m.experiment_inputs = pyo.Suffix(
            direction=pyo.Suffix.LOCAL,
        )
        m.experiment_inputs.update((k, pyo.ComponentUID(k)) for k in expand_model_components(m, base_comp_des, index_sets_des))


class FullReactorExperiment(ReactorExperiment):
    def label_experiment(self):
        m = self.model
        return self.label_experiment_impl([[m.t_control], [m.t_control], [m.t_control]])


class PartialReactorExperiment(ReactorExperiment):
    def label_experiment(self):
        """
        Example for annotating (labeling) the model with a
        "partial" experiment.

        Arguments
        ---------

        """
        m = self.model
        return self.label_experiment_impl([[m.t_control], [[m.t.last()]], [[m.t.last()]]])


f = open('result.json')
data_ex = json.load(f)
data_ex['control_points'] = {float(k): v for k, v in data_ex['control_points'].items()}

experiments = [
    FullReactorExperiment(data_ex, 32, 3),
    PartialReactorExperiment(data_ex, 32, 3),
]

# in parmest / DoE:
expanded_experiments = [e.get_labeled_model() for e in experiments]